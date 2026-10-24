//! Small independently tested host-side lifecycle rules.
use std::{
    net::{IpAddr, SocketAddr},
    path::{Path, PathBuf},
    process::Command,
};

pub fn loopback_address(output: &str) -> Result<SocketAddr, String> {
    let addresses: Vec<_> = output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .collect();
    if addresses.len() != 1 {
        return Err("Docker returned an ambiguous runtime port".into());
    }
    let address: SocketAddr = addresses[0]
        .trim()
        .parse()
        .map_err(|_| "Docker returned an invalid runtime port")?;
    if address.ip() != IpAddr::from([127, 0, 0, 1]) || address.port() == 0 {
        return Err("Desktop runtime must bind only to 127.0.0.1 on an assigned port".into());
    }
    Ok(address)
}

pub fn directories(app_data: &Path) -> Result<(PathBuf, PathBuf), String> {
    let projects = app_data.join("projects");
    let runtime = app_data.join("runtime");
    for path in [&projects, &runtime] {
        if path.is_symlink() {
            return Err("Desktop data paths cannot be symbolic links".into());
        }
        std::fs::create_dir_all(path)
            .map_err(|error| format!("Cannot access persistent desktop data: {error}"))?;
    }
    Ok((projects, runtime))
}

pub fn process_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        Command::new("kill")
            .args(["-0", &pid.to_string()])
            .output()
            .is_ok_and(|output| output.status.success())
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        true
    } // Never claim another process is dead when host evidence is unavailable.
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_unambiguous_loopback_ports_are_accepted() {
        assert_eq!(loopback_address("127.0.0.1:48127\n").unwrap().port(), 48127);
        for input in [
            "0.0.0.0:8080",
            "127.0.0.1:0",
            "example.org:8000",
            "127.0.0.1:8\n127.0.0.1:9",
            "",
        ] {
            assert!(loopback_address(input).is_err());
        }
    }
    #[test]
    fn persistent_project_data_survives_runtime_directory_reopen() {
        let root = std::env::temp_dir().join(format!("oxygen-lifecycle-{}", std::process::id()));
        let (projects, _) = directories(&root).unwrap();
        std::fs::write(projects.join("user-data"), "keep").unwrap();
        directories(&root).unwrap();
        assert_eq!(
            std::fs::read_to_string(projects.join("user-data")).unwrap(),
            "keep"
        );
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn live_host_process_is_not_reaped() {
        assert!(process_alive(std::process::id()));
    }
}
