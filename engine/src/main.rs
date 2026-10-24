//! Headless JSON-lines boundary for Oxygen CAD's vendored SolidCraft engine.
#![forbid(unsafe_code)]

use serde_json::{Value, json};
use solidcraft_engine::Session;
use std::io::{self, BufRead, Read, Write};

const REVISION: &str = "0520910bfd4ff6d24c850f4a20a604f6128f87bf";

fn new_session(document: solidcraft_engine::doc::Document) -> Result<Session, String> {
    let mut session = Session::new(document);
    let host = solidcraft_engine::render::job::CpuHost::for_platform().map_err(|e| e.to_string())?;
    session.set_render_job_host(Some(host)).map_err(|e| e.to_string())?;
    Ok(session)
}

fn dispatch(session: &mut Session, request: &Value) -> Result<Value, String> {
    let command = request
        .get("command")
        .and_then(Value::as_str)
        .ok_or("Missing command")?;
    if command == "oxygen.version" {
        return Ok(
            json!({"engine":"solidcraft", "upstream_commit":REVISION, "protocol":1, "language":"rust"}),
        );
    }
    if command == "oxygen.licenses" {
        return Ok(json!({"licenses":solidcraft_engine::licences::all()}));
    }
    if command == "oxygen.reset" {
        *session = new_session(solidcraft_engine::doc::Document::new("Untitled"))?;
        return Ok(json!({"reset":true}));
    }
    // The host persists the upstream versioned document, not a second feature language.
    // Document::from_json applies upstream migrations and rejects future formats.
    if command == "oxygen.document.set" {
        let params = request.get("params").ok_or("Missing params")?;
        let document = params.get("document").ok_or("Missing document")?;
        let document = solidcraft_engine::doc::Document::from_json(&document.to_string())
            .map_err(|e| e.to_string())?;
        let mut restored = new_session(document)?;
        if let Some(context) = params.get("context") {
            restored.active_sketch = context.get("active_sketch").and_then(Value::as_u64);
            restored.active_component = context.get("active_component").and_then(Value::as_u64).unwrap_or(0);
            restored.active_occurrence_path = serde_json::from_value(context.get("active_occurrence_path").cloned().unwrap_or(Value::Null)).map_err(|e| e.to_string())?;
            restored.selection = serde_json::from_value(context.get("selection").cloned().unwrap_or(json!([]))).map_err(|e| e.to_string())?;
            restored.pending_moves = serde_json::from_value(context.get("pending_moves").cloned().unwrap_or(json!({}))).map_err(|e| e.to_string())?;
            if restored.active_sketch.is_some_and(|id| restored.doc.feature(id).is_none()) {
                return Err("Active sketch does not exist in the native document".into());
            }
        }
        *session = restored;
        return Ok(json!({"format": session.doc.format}));
    }
    if command == "oxygen.document.get" {
        let document = serde_json::to_value(session.design().as_ref()).map_err(|e| e.to_string())?;
        return Ok(json!({"document":document,"context":{
            "active_sketch":session.active_sketch,"active_component":session.active_component,
            "active_occurrence_path":session.active_occurrence_path,"selection":session.selection,
            "pending_moves":session.pending_moves,
        }}));
    }
    if command == "oxygen.scene" {
        let include_mesh = request.get("params").and_then(|p| p.get("mesh")).and_then(Value::as_bool).unwrap_or(true);
        let world = session.world_state();
        let bodies: Vec<Value> = world.bodies.iter().map(|b| {
            let validity = b.body.validity();
            let kind = if b.body.is_mesh() { "mesh" }
                else if validity.iter().any(|e| e.contains("not closed")) { "surface" } else { "solid" };
            let bounds = b.outline_bounds();
            let mut value = json!({
                "id":format!("body:{}:{}:{}",b.component,b.feature,b.name),
                "name":b.name,"definition_name":b.definition_name(),"feature":b.feature,"component":b.component,
                "geometry_kind":kind,"visible":session.body_visible(&b.name),"validity":validity,
                "transform":b.source_transform,"bbox":{"min":bounds.min,"max":bounds.max},
                "face_count":b.body.face_count(),"edge_count":b.body.edge_count(),
            });
            if include_mesh {
                value["mesh"] = serde_json::to_value(b.mesh().as_ref()).unwrap_or(Value::Null);
            }
            value
        }).collect();
        return Ok(json!({"schema_version":1,"bodies":bodies}));
    }
    // Upstream break_link detaches stored sketch geometry. Materialize the current
    // evaluated projection first, so a parameter edit cannot detach an older silhouette.
    // This is a host adapter correction; the pinned dependency remains unchanged.
    let before_host_adapter = session.doc.clone();
    if command == "sketch.break_link" {
        if let Some(id) = session.active_sketch {
            let state = session.model.state();
            if let Some(evaluated) = state.sketch(id) {
                let sketch = evaluated.sketch.clone();
                *session.doc_mut().sketch_mut(id).map_err(|e| e.to_string())? = sketch;
            }
        }
    }
    let errors = |session: &Session| -> Vec<String> {
        session
            .model
            .results
            .iter()
            .filter_map(|r| r.error.as_ref().map(|e| format!("{}: {e}", r.name)))
            .collect()
    };
    // Refuse writes before invoking a file command if a previous edit invalidated the timeline.
    if command.starts_with("file.") && !errors(session).is_empty() {
        return Err(
            "The timeline has failed features; repair it before saving or exporting".into(),
        );
    }
    let mut result = match session.execute(command, request.get("params").unwrap_or(&json!({}))) {
        Ok(result) => result,
        Err(error) => {
            if command == "sketch.break_link" {
                session.doc = before_host_adapter;
                session.refresh();
            }
            return Err(error.to_string());
        }
    };
    // A divergence-theorem volume of an open shell is origin-dependent, not a
    // physical volume. Preserve its measured area, but never publish fictitious mass.
    if matches!(command, "inspect.measure" | "document.inspect") {
        let world = session.world_state();
        if let Some(rows) = result.get_mut("bodies").and_then(Value::as_array_mut) {
            for row in rows.iter_mut() {
                if let Some(body) = world.bodies.iter().find(|b| row["name"].as_str() == Some(&b.name)) {
                    let open = !body.body.is_mesh() && body.body.validity().iter().any(|e| e.contains("not closed"));
                    row["geometry_kind"] = json!(if body.body.is_mesh() { "mesh" } else if open { "surface" } else { "solid" });
                    if open {
                        row["volume_mm3"] = Value::Null;
                        row["mass_g"] = Value::Null;
                        row["center_of_mass"] = Value::Null;
                    }
                }
            }
            let volume: f64 = rows.iter().filter_map(|b| b["volume_mm3"].as_f64()).sum();
            let mass: f64 = rows.iter().filter_map(|b| b["mass_g"].as_f64()).sum();
            if result.get("total").is_some() {
                result["total"]["volume_mm3"] = json!(volume);
                result["total"]["mass_g"] = json!(mass);
                result["total"]["volume_basis"] = json!("closed_bodies_only");
            }
        }
    }
    let failures = errors(session);
    if !failures.is_empty() {
        return Err(format!(
            "Feature evaluation failed: {}",
            failures.join("; ")
        ));
    }
    Ok(result)
}

fn main() -> io::Result<()> {
    let mut session = new_session(solidcraft_engine::doc::Document::new("Untitled")).map_err(io::Error::other)?;
    let mut input = io::stdin().lock();
    let mut output = io::stdout().lock();
    loop {
        let mut line = Vec::new();
        let read = input
            .by_ref()
            .take((solidcraft_engine::MAX_LINE + 1) as u64)
            .read_until(b'\n', &mut line)?;
        if read == 0 {
            break;
        }
        if line.len() > solidcraft_engine::MAX_LINE {
            writeln!(
                output,
                "{}",
                json!({"ok":false,"error":"Request exceeds 16 MiB"})
            )?;
            output.flush()?;
            break;
        }
        let reply = match serde_json::from_slice::<Value>(&line) {
            Ok(request) => match dispatch(&mut session, &request) {
                Ok(result) => json!({"ok":true,"result":result}),
                Err(error) => json!({"ok":false,"error":error}),
            },
            Err(error) => json!({"ok":false,"error":format!("Invalid JSON: {error}")}),
        };
        writeln!(output, "{reply}")?;
        output.flush()?;
    }
    Ok(())
}
