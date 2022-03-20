import argparse
from argparse import Namespace

import subprocess
from datetime import datetime
from typing import Generic, TypeVar

# When you want to write Rust but you use python
T = TypeVar('T')
class Ok(Generic[T]):
    def __init__(self, value: T) -> None:
        super().__init__()
        self.value = value
class Err:
    def __init__(self, reason: str) -> None:
        self.reason =  reason

Result = Ok[T] | Err


def parse_args() -> Namespace:
    parser = argparse.ArgumentParser(
        description="Given a reference to a flatpak, try to reproduce it and"
        "compare to the one from the repo."
    )
    parser.add_argument('remote', help="The name of the remote repository, i.e. flathub")
    parser.add_argument('flatpak_name', help="The name of the flatpak to reproduce")

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

def init_local_installation(path: str | None):
    if path is None:
        return
    else:
        return
    

def main():
    args = parse_args()
    metadatas = fetch_info_from_remote(args.remote, args.flatpak_name)
    match metadatas:
        case Ok(value=value):
            runtime = value['Sdk']
            date = flatpak_date_to_datetime(value['Date'])
            sdk_runtime_commit = find_runtime_commit_for_date(args.remote, runtime, date)
        case Err(reason=reason):
            print(reason)

if __name__ == '__main__':
    main()
