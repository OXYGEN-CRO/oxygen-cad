//! Headless JSON-lines boundary for Oxygen CAD's vendored SolidCraft engine.
#![forbid(unsafe_code)]

use serde_json::{Value, json};
use solidcraft_engine::Session;
use std::io::{self, BufRead, Read, Write};

const REVISION: &str = "0520910bfd4ff6d24c850f4a20a604f6128f87bf";

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
        *session = Session::default();
        return Ok(json!({"reset":true}));
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
    let result = session
        .execute(command, request.get("params").unwrap_or(&json!({})))
        .map_err(|e| e.to_string())?;
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
    let mut session = Session::default();
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
