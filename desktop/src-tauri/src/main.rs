#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
#![forbid(unsafe_code)]

use serde_json::{Value, json};
use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::Command,
    sync::Mutex,
    thread,
    time::Duration,
};
use tauri::Manager;

#[derive(Default)]
struct Runtime {
    message: String,
    error: bool,
    container: Option<String>,
    docker: Option<PathBuf>,
    starting: bool,
}

fn state(app: &tauri::AppHandle, message: &str, error: bool) {
    if let Ok(mut s) = app.state::<Mutex<Runtime>>().lock() {
        s.message = message.into();
        s.error = error;
    }
}

#[tauri::command]
fn startup_status(app: tauri::AppHandle) -> String {
    match app.state::<Mutex<Runtime>>().lock() {
        Ok(s) => json!({"message":s.message,"error":s.error}).to_string(),
        Err(_) => {
            json!({"message":"The startup state is unavailable. Restart Oxygen CAD.","error":true})
                .to_string()
        }
    }
}

fn docker_binary() -> Result<PathBuf, String> {
    for path in [
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
        "/usr/bin/docker",
        "docker",
    ] {
        if Command::new(path)
            .arg("--version")
            .output()
            .is_ok_and(|r| r.status.success())
        {
            return Ok(path.into());
        }
    }
    Err("Install and open Docker Desktop, then try again. Oxygen uses it for local CAD and simulation.".into())
}

fn run(docker: &Path, args: &[&str]) -> Result<String, String> {
    let result = Command::new(docker)
        .args(args)
        .output()
        .map_err(|e| e.to_string())?;
    if !result.status.success() {
        let text = String::from_utf8_lossy(&result.stderr);
        let tail: String = text
            .chars()
            .rev()
            .take(1800)
            .collect::<String>()
            .chars()
            .rev()
            .collect();
        return Err(tail);
    }
    Ok(String::from_utf8_lossy(&result.stdout).trim().to_owned())
}

fn ready(url: &tauri::Url) -> bool {
    let Some(port) = url.port_or_known_default() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_millis(500),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    if stream
        .write_all(b"GET /api/project HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut bytes = [0u8; 512];
    stream
        .read(&mut bytes)
        .is_ok_and(|n| String::from_utf8_lossy(&bytes[..n]).starts_with("HTTP/1.1 200"))
}

fn launch(app: &tauri::AppHandle) -> Result<(), String> {
    // Development smoke tests can attach the native window to an existing local runtime.
    if let Ok(value) = std::env::var("O3D_DESKTOP_URL") {
        let url = tauri::Url::parse(&value).map_err(|e| e.to_string())?;
        if url.scheme() != "http" || !matches!(url.host_str(), Some("127.0.0.1")) {
            return Err("The development runtime must use http://127.0.0.1".into());
        }
        if !ready(&url) {
            return Err("The local development runtime is not ready".into());
        }
        return app
            .get_webview_window("main")
            .ok_or("Window missing")?
            .navigate(url)
            .map_err(|e| e.to_string());
    }
    let docker = docker_binary()?;
    state(app, "Connecting to Docker Desktop…", false);
    run(&docker, &["info", "--format", "{{.ServerVersion}}"])
        .map_err(|_| "Open Docker Desktop and wait for it to start, then try again.".to_string())?;
    let resource = app
        .path()
        .resource_dir()
        .map_err(|e| e.to_string())?
        .join("runtime");
    let config: Value = serde_json::from_slice(
        &std::fs::read(resource.join("runtime.json")).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    let image = config
        .get("image")
        .and_then(Value::as_str)
        .ok_or("Runtime image missing")?;
    if run(&docker, &["image", "inspect", image]).is_err() {
        if config.get("mode").and_then(Value::as_str) == Some("image") {
            state(
                app,
                "Preparing your local CAD runtime. This only happens on first launch…",
                false,
            );
            let archive = resource.join("runtime.tar.gz");
            run(
                &docker,
                &[
                    "load",
                    "--input",
                    archive.to_str().ok_or("Invalid runtime path")?,
                ],
            )?;
        } else {
            state(
                app,
                "Building your local CAD runtime. The first launch takes several minutes…",
                false,
            );
            run(
                &docker,
                &[
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--tag",
                    image,
                    resource.to_str().ok_or("Invalid runtime path")?,
                ],
            )?;
        }
    }
    let projects = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("projects");
    std::fs::create_dir_all(&projects).map_err(|e| e.to_string())?;
    let container = format!("oxygen-cad-desktop-{}", std::process::id());
    let mount = format!("type=bind,source={},target=/projects", projects.display());
    let mut args = vec![
        "run".to_string(),
        "--detach".into(),
        "--rm".into(),
        "--platform".into(),
        "linux/amd64".into(),
        "--name".into(),
        container.clone(),
        "--publish".into(),
        "127.0.0.1::8787".into(),
        "--mount".into(),
        mount,
    ];
    if let (Ok(uid), Ok(gid)) = (
        Command::new("id").arg("-u").output(),
        Command::new("id").arg("-g").output(),
    ) {
        if uid.status.success() && gid.status.success() {
            args.extend([
                "--user".into(),
                format!(
                    "{}:{}",
                    String::from_utf8_lossy(&uid.stdout).trim(),
                    String::from_utf8_lossy(&gid.stdout).trim()
                ),
            ]);
        }
    }
    args.push(image.to_owned());
    state(app, "Opening your workspace…", false);
    let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
    run(&docker, &borrowed)?;
    if let Ok(mut s) = app.state::<Mutex<Runtime>>().lock() {
        s.container = Some(container.clone());
        s.docker = Some(docker.clone());
    }
    let address = run(&docker, &["port", &container, "8787/tcp"])?;
    let url = tauri::Url::parse(&format!("http://{address}")).map_err(|e| e.to_string())?;
    for _ in 0..120 {
        if ready(&url) {
            return app
                .get_webview_window("main")
                .ok_or("Window missing")?
                .navigate(url)
                .map_err(|e| e.to_string());
        }
        thread::sleep(Duration::from_millis(500));
    }
    Err(
        "The local runtime did not start. Quit Oxygen CAD, check Docker Desktop, and reopen it."
            .into(),
    )
}

#[tauri::command]
fn retry(app: tauri::AppHandle) {
    if let Ok(mut s) = app.state::<Mutex<Runtime>>().lock() {
        if s.starting {
            return;
        }
        s.starting = true;
        s.error = false;
    }
    thread::spawn(move || {
        if let Err(error) = launch(&app) {
            state(&app, &error, true);
        }
        if let Ok(mut s) = app.state::<Mutex<Runtime>>().lock() {
            s.starting = false;
        }
    });
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Mutex::new(Runtime {
            message: "Starting your local workspace…".into(),
            ..Default::default()
        }))
        .invoke_handler(tauri::generate_handler![startup_status, retry])
        .setup(|app| {
            retry(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!());
    match app {
        Ok(app) => app.run(|handle, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                if let Ok(s) = handle.state::<Mutex<Runtime>>().lock() {
                    if let (Some(docker), Some(container)) = (&s.docker, &s.container) {
                        let _ = run(docker, &["stop", "--time", "2", container]);
                    }
                }
            }
        }),
        Err(error) => {
            eprintln!("Oxygen CAD: {error}");
            std::process::exit(1);
        }
    }
}
