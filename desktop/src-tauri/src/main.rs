#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
#![forbid(unsafe_code)]

mod lifecycle;

use serde_json::{Value, json};
use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::Manager;

#[derive(Default)]
struct Runtime {
    message: String,
    error: bool,
    container: Option<String>,
    docker: Option<PathBuf>,
    starting: bool,
    shutting_down: bool,
    lease: Option<PathBuf>,
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
    let mut child = Command::new(docker)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    fn capture(mut pipe: impl Read + Send + 'static) -> thread::JoinHandle<Vec<u8>> {
        thread::spawn(move || {
            let mut tail = Vec::new();
            let mut chunk = [0u8; 4096];
            while let Ok(count) = pipe.read(&mut chunk) {
                if count == 0 {
                    break;
                }
                tail.extend_from_slice(&chunk[..count]);
                if tail.len() > 65536 {
                    tail.drain(..tail.len() - 65536);
                }
            }
            tail
        })
    }
    let stdout = capture(child.stdout.take().ok_or("Docker stdout unavailable")?);
    let stderr = capture(child.stderr.take().ok_or("Docker stderr unavailable")?);
    let timeout = if matches!(args.first(), Some(&"build") | Some(&"load")) {
        Duration::from_secs(1800)
    } else {
        Duration::from_secs(30)
    };
    let started = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        if started.elapsed() > timeout {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!(
                "Docker {} timed out; existing projects remain in application data",
                args.first().unwrap_or(&"command")
            ));
        }
        thread::sleep(Duration::from_millis(100));
    };
    let out = stdout.join().unwrap_or_default();
    let err = stderr.join().unwrap_or_default();
    if !status.success() {
        return Err(format!(
            "Docker {} failed: {}",
            args.first().unwrap_or(&"command"),
            String::from_utf8_lossy(&err)
        ));
    }
    Ok(String::from_utf8_lossy(&out).trim().to_owned())
}

fn cleanup(app: &tauri::AppHandle) {
    let ownership = app
        .state::<Mutex<Runtime>>()
        .lock()
        .ok()
        .and_then(|mut s| Some((s.docker.take()?, s.container.take()?, s.lease.take())));
    if let Some((docker, container, lease)) = ownership {
        if run(&docker, &["rm", "--force", &container]).is_ok() {
            if let Some(path) = lease {
                let _ = std::fs::remove_file(path);
            }
        }
    }
}

fn reclaim_abandoned(docker: &Path, directory: &Path, scope: &str) {
    let Ok(entries) = std::fs::read_dir(directory) else {
        return;
    };
    for entry in entries.flatten() {
        if entry.path().extension().and_then(|v| v.to_str()) != Some("json") {
            continue;
        }
        let Ok(bytes) = std::fs::read(entry.path()) else {
            continue;
        };
        let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
            continue;
        };
        let Some(pid) = value.get("pid").and_then(Value::as_u64) else {
            continue;
        };
        if lifecycle::process_alive(pid as u32)
            || value.get("scope").and_then(Value::as_str) != Some(scope)
        {
            continue;
        }
        let Some(container) = value.get("container").and_then(Value::as_str) else {
            continue;
        };
        // A lease never authorizes deleting an arbitrary container: inspect the ownership label.
        let label = run(
            docker,
            &[
                "inspect",
                "--format",
                "{{index .Config.Labels \"io.oxygen.desktop.scope\"}}",
                container,
            ],
        );
        if label.as_deref() == Ok(scope) && run(docker, &["rm", "--force", container]).is_ok() {
            let _ = std::fs::remove_file(entry.path());
        }
    }
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
        .write_all(
            b"GET /api/platform/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
        )
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
        if url.scheme() != "http"
            || !matches!(url.host_str(), Some("127.0.0.1"))
            || !url.username().is_empty()
            || url.password().is_some()
        {
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
    if config.get("schema_version").and_then(Value::as_u64) != Some(1) {
        return Err("This desktop runtime format is unsupported. Reinstall a compatible release; your projects are retained.".into());
    }
    let image = config
        .get("image")
        .and_then(Value::as_str)
        .ok_or("Runtime image missing")?;
    if !(image.starts_with("sha256:") || image.starts_with("oxygen-cad-desktop:")) {
        return Err("Desktop runtime image identity is invalid".into());
    }
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
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let (projects, leases) = lifecycle::directories(&data)?;
    let scope_path = leases.join("scope");
    let scope = if scope_path.exists() {
        std::fs::read_to_string(&scope_path).map_err(|e| e.to_string())?
    } else {
        let value = format!(
            "{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|e| e.to_string())?
                .as_nanos()
        );
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&scope_path)
        {
            Ok(mut file) => {
                file.write_all(value.as_bytes())
                    .map_err(|e| e.to_string())?;
                file.sync_all().map_err(|e| e.to_string())?;
                value
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                std::fs::read_to_string(&scope_path).map_err(|e| e.to_string())?
            }
            Err(error) => return Err(error.to_string()),
        }
    };
    reclaim_abandoned(&docker, &leases, &scope);
    let instance = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let container = format!("oxygen-cad-desktop-{}-{instance}", std::process::id());
    let lease = leases.join(format!("{}-{instance}.json", std::process::id()));
    let scope_label = format!("io.oxygen.desktop.scope={scope}");
    let mount = format!("type=bind,source={},target=/projects", projects.display());
    let mut args = vec![
        "run".to_string(),
        "--detach".into(),
        "--rm".into(),
        "--platform".into(),
        "linux/amd64".into(),
        "--name".into(),
        container.clone(),
        "--label".into(),
        scope_label,
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
    // Persist ownership before creating the container, so a killed startup is reclaimable.
    std::fs::write(
        &lease,
        json!({"pid":std::process::id(),"container":container,"scope":scope,"image":image})
            .to_string(),
    )
    .map_err(|e| e.to_string())?;
    if let Ok(mut s) = app.state::<Mutex<Runtime>>().lock() {
        s.container = Some(container.clone());
        s.docker = Some(docker.clone());
        s.lease = Some(lease);
    }
    run(&docker, &borrowed)?;
    if let Ok(mut s) = app.state::<Mutex<Runtime>>().lock() {
        s.container = Some(container.clone());
        s.docker = Some(docker.clone());
        if s.shutting_down {
            return Err("Desktop is closing".into());
        }
    }
    let address = run(&docker, &["port", &container, "8787/tcp"])?;
    let address = lifecycle::loopback_address(&address)?;
    let url = tauri::Url::parse(&format!("http://{address}")).map_err(|e| e.to_string())?;
    for _ in 0..120 {
        if app
            .state::<Mutex<Runtime>>()
            .lock()
            .is_ok_and(|s| s.shutting_down)
        {
            return Err("Desktop is closing".into());
        }
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
        if s.starting || s.shutting_down {
            return;
        }
        s.starting = true;
        s.error = false;
    }
    thread::spawn(move || {
        cleanup(&app);
        if let Err(error) = launch(&app) {
            cleanup(&app);
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
                if let Ok(mut s) = handle.state::<Mutex<Runtime>>().lock() {
                    s.shutting_down = true;
                }
                cleanup(handle);
            }
        }),
        Err(error) => {
            eprintln!("Oxygen CAD: {error}");
            std::process::exit(1);
        }
    }
}
