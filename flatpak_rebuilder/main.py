import argparse
from argparse import Namespace

import subprocess
import os
from git.repo import Repo
from datetime import datetime
import time
from typing import Generic, TypeVar

# When you want to write Rust but you use python
T = TypeVar('T')
class Ok(Generic[T]):
    def __init__(self, value: T) -> None:
        super().__init__()
        self.value = value
    def is_ok(self) -> bool:
        return True
    def is_err(self) -> bool:
        return False 
    def unwrap(self) -> T:
        return self.value

class Err(Generic[T]):
    def __init__(self, reason: str) -> None:
        self.reason =  reason
    def is_ok(self) -> bool:
        return False 
    def is_err(self) -> bool:
        return True
    def unwrap(self) -> T:
        raise Exception("Tried to unwrap an Err, which had the following erro: " + self.reason)

Result = Ok[T] | Err[T]


def parse_args() -> Namespace:
    parser = argparse.ArgumentParser(
        description="Given a reference to a flatpak, try to reproduce it and"
        "compare to the one from the repo."
    )
    parser.add_argument('remote', help="The name of the remote repository, i.e. flathub")
    parser.add_argument('flatpak_name', help="The name of the flatpak to reproduce")
    parser.add_argument('-i','--installation',help="specifies the local installation to use, by default it will use the global flatpak install.")
    parser.add_argument('-int','--interactive',help="If set, flatpaks install and build command will run in interactive mode, asking you for input.", action='store_true')

    return parser.parse_args()

def fetch_info_from_remote(remote: str, package: str) -> Result[dict[str, str]]:
    result = subprocess.run(["flatpak", "remote-info", remote, package], capture_output=True) 
    if result.returncode == 0:
        output = result.stdout.decode('UTF-8')
        return Ok(cmd_output_to_dict(output))
    return Err(result.stderr.decode('UTF-8'))

def find_runtime_commit_for_date(remote: str, runtime: str, date: datetime) -> Result[str]:
    result = subprocess.run(["flatpak", "remote-info", remote, runtime, "--log"], capture_output=True)
    if result.returncode == 0:
        output = result.stdout.decode('UTF-8')
        commits = output.split("\n\n")[1:]
        # We use the fact that --log return commits from the most recent to the oldest
        for commit in commits:
            commit = cmd_output_to_dict(commit)
            commit_date = flatpak_date_to_datetime(commit['Date'])
            if commit_date <= date:
                return Ok(commit['Commit'])
        return Err("No commit matching the date have been found")
    return Err(result.stderr.decode('UTF-8'))

def cmd_output_to_dict(output: str) -> dict[str, str]:
    result = [map(str.strip, line.split(':', 1)) for line in output.split('\n') if ':' in line]
    resultDict: dict[str, str] = dict(result)
    return resultDict

def flatpak_date_to_datetime(date: str) -> datetime:
    split = date.split('+')
    time = split[0]
    time_zone = split[1]
    time_zone = '+' + time_zone[:2] + ':' + time_zone[2:]
    return datetime.fromisoformat(time + time_zone)

def init_local_installation(path: str, name='rebuilder'):
    config_file = "[]"

def installation_exists(name: str) -> bool:
    result = subprocess.run(["flatpak", "--installation="+name,"list"]) 
    return result.returncode == 0

def setup_build_dir(git_url: str, dir: str, build_time: datetime):
    build_time_float = time.mktime(build_time.timetuple())
    os.mkdir(dir)
    path = "./"+dir
    repo = Repo.clone_from(git_url,path)
    repo.submodule_update()
    for root, dirs, files in os.walk(path):
        for file in files:
            # Try to only touch manifest files
            if file.endswith(".json") or file.endswith(".yml"):
                os.utime(os.path.join(root, file), (build_time_float, build_time_float))

def install_runtime(remote: str, runtime: str, installation: str, interactive: bool, commit: str | None = None) -> Result[None]:
    if not installation_exists(installation):
        return Err(installation + " is not a valid flatpak installation, please set it up manually.")

    cmd = ["flatpak", "install", remote, runtime, "--installation="+installation]
    if not interactive:
        cmd.append('--noninteractive')

    install = subprocess.run(cmd) 

    if install.returncode != 0:
       return Err(install.stderr.decode('UTF-8')) 

    cmd = ["flatpak", "update", runtime, "--installation="+installation]
    if commit is not None:
        cmd.append("--commit="+commit)
    if not interactive:
        cmd.append("--noninteractive")

    downgrade = subprocess.run(cmd) 

    if downgrade.returncode != 0:
       return Err(downgrade.stderr.decode('UTF-8')) 
    return Ok(None)

def get_build_repo(remote: str, package: str) -> Result[str]:
    match remote:
        case "flathub":
            return Ok("https://github.com/flathub/"+package) 
        case _:
            return Err("Only flathub is supported for now.")

def rebuild(dir: str, installation: str):
    os.chdir(dir)
    manifests = [file for file in os.listdir() if (file.endswith(".yml") or file.endswith(".json"))]
    if len(manifests) > 1:
        pass
    else:
        manifest = manifests[0]
    cmd = ["flatpak-builder", "--disable-cache", "--force-clean", "--installation="+installation, "build", manifest]
    subprocess.run(cmd)

def main():
    args = parse_args()
    remote = args.remote
    package = args.flatpak_name
    metadatas = fetch_info_from_remote(remote, package).unwrap()
    sdk_runtime = metadatas['Sdk']
    runtime = metadatas['Runtime']
    if args.installation is not None:
        installation = args.installation
    else:
        installation = "default"
    interactive = args.interactive
    date = flatpak_date_to_datetime(metadatas['Date'])
    sdk_runtime_commit = find_runtime_commit_for_date(args.remote, sdk_runtime, date).unwrap()
    install_runtime(remote, runtime,interactive=interactive, installation=installation)
    install_runtime(remote, sdk_runtime,interactive=interactive, installation=installation,commit=sdk_runtime_commit)
    build_repo = get_build_repo(remote, package).unwrap()
    setup_build_dir(build_repo, package, date) 
    rebuild(package, installation)

if __name__ == '__main__':
    main()
