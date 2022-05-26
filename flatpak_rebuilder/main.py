import argparse
from argparse import Namespace
import subprocess
import os
import re
import shutil
from git.repo import Repo
from datetime import datetime
import time
from datetime import timezone
from pathlib import Path
import json
from checksumdir import dirhash
import sys

FLATPAK_BUILDER = "org.flatpak.Builder"


class GitNotFoundException(Exception):
    pass


class FlatpakCmdException(Exception):
    def __init__(self, cmd: list[str], msg: str = ""):
        super().__init__(f"Failed to run {cmd}, with the following error: {msg}")


def run_flatpak_command(
    cmd: list[str],
    installation: str,
    may_need_root=False,
    capture_output=False,
    cwd: str | None = None,
    interactive=True,
    arch: str | None = None,
    check_returncode=True,
    include_stderr=False,
) -> str:
    """Runs a flatpak shell command.

    Parameters
    ----------
    cmd
        The command to execute.
    installation
        The flatpak installation to use (i.e user, system).
    may_need_root : bool, optional
        If true will use sudo if it runs with a system install.
    capture_output : bool, optional
        If true, will capture and return the output
    cwd: str, optional
        Run the command in this directory (if set).
    interactive : bool, optional
        Run the command in interactive mode.
    arch: str, optional
        Add the --arch=<arch> flag to the command.
    check_returncode : bool, optional
        If true, will check return code and throw exception if not 0.
    include_stderr: bool, optional
        If true, will pipe stderr in stdout and return it (regardless of capture_output).

    Returns
    -------
    str
        Empty if no capture flag set, the command output (either stdout or stderr) otherwise.

    Raises
    ------
    Exception
        If check_returncode = True and the command returns a non zero code.
    """
    if may_need_root and installation != "user":
        cmd.insert(0, "sudo")

    cmd.append(flatpak_installation_flag(installation))

    if arch:
        cmd.append("--arch=" + arch)

    if not interactive:
        cmd.append("--noninteractive")

    if include_stderr:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd
        )
    elif capture_output:
        result = subprocess.run(cmd, capture_output=True, cwd=cwd)
    else:
        result = subprocess.run(cmd, cwd=cwd)

    if result.returncode != 0 and check_returncode:
        if include_stderr:
            raise FlatpakCmdException(cmd, result.stdout.decode("UTF-8"))
        elif capture_output:
            raise FlatpakCmdException(cmd, result.stderr.decode("UTF-8"))
        else:
            raise FlatpakCmdException(cmd)
    else:
        if capture_output or include_stderr:
            return result.stdout.decode("UTF-8")
        else:
            return ""


def flatpak_installation_flag(installation: str) -> str:
    match installation:
        case "user":
            return "--user"
        case "system":
            return "--system"
        case _:
            return "--installation=" + installation


def parse_args() -> Namespace:
    """Parse the different command arguments."""

    parser = argparse.ArgumentParser(
        description="Given a reference to a flatpak, try to reproduce it and"
        "compare to the one from the repo."
    )
    parser.add_argument("flatpak_name", help="The name of the flatpak to reproduce")
    parser.add_argument(
        "-d",
        "--diffoscope",
        help="When enabled, diffoscope will be use to diff the two results.",
        action="store_true",
    )
    parser.add_argument(
        "-s",
        "--strip",
        help="When enabled, strip-nondeterminism will be run on the two results before diffing.",
        action="store_true",
    )
    parser.add_argument(
        "-int",
        "--interactive",
        help="When enabled, flatpaks install and build command will run in interactive mode, asking you for input.",
        action="store_true",
    )
    parser.add_argument(
        "-c", "--commit", help="Commit number of the package to rebuild."
    )
    parser.add_argument("-t", "--time", help="Time to use for the rebuild.")
    parser.add_argument(
        "-a",
        "--arch",
        help="Cpu architeture to use for the build, by default will use the one available on the system.",
    )
    parser.add_argument(
        "--branch",
        help="Specify which branch of the flatpak to use, will use the latest one if not specified.",
    )
    parser.add_argument(
        "--estimate-time",
        help="Let flatpak rebuilder find a time estimate of the build by scraping binaries (it rarely works).",
        action="store_true",
    )
    parser.add_argument(
        "--beta", help="Use the beta branch of the package.", action="store_true"
    )

    install_group = parser.add_mutually_exclusive_group()
    install_group.add_argument(
        "-i",
        "--installation",
        help="Specify the flatpak installation to use, will use the user installation by default.",
    )
    install_group.add_argument(
        "--user", help="When enabled, use user install.", action="store_true"
    )
    install_group.add_argument(
        "--system", help="When enabled, use system install.", action="store_true"
    )

    return parser.parse_args()


def flatpak_info(installation: str, package: str) -> dict[str, str]:
    """Give information about a locally installed package.

    Parameters
    ----------
    installation : str
        The flatpak installtion in which to search the package.
    package : str
        Name of the package.

    Returns
    -------
    dict[str, str]
    """
    cmd = ["flatpak", "info", package]
    output = run_flatpak_command(cmd, installation, capture_output=True)
    return cmd_output_to_dict(output)


def get_available_branches(
    remote: str, installation: str, package: str, arch: str
) -> list[str]:
    """Returns all the available branches of a package id in remote"""
    cmd = ["flatpak", "remote-info", remote, package, f"--arch={arch}"]
    output = run_flatpak_command(
        cmd, installation, check_returncode=False, include_stderr=True
    )
    # If the command fail, the output will contain the list of possible branches
    if "Multiple branches available" in output:
        branches = map(
            lambda s: s.strip().split("/")[-1],
            reversed(output.split(":")[2].split(",")),
        )
        return list(branches)
    else:
        metadatas = cmd_output_to_dict(output)
        return [metadatas["Branch"]]


def cmd_output_to_dict(output: str) -> dict[str, str]:
    """Format commands with output of the form 'key : value' into a dictionary"""
    result = [
        list(map(str.strip, line.split(":", 1)))
        for line in output.split("\n")
        if ":" in line
    ]
    resultDict: dict[str, str] = dict(result)
    return resultDict


def flatpak_install(
    remote: str,
    package: str,
    installation: str,
    interractive: bool,
    arch: str,
    or_update: bool = False,
    no_deps: bool = False,
):
    """Install a flatpak

    Parameters
    ----------
    remote : str
        Name of the remote (i.e flathub).
    package : str
        Name of the package.
    installation: str
        Name of the installation (i.e user)
    interactive: bool
        Run command in interactive mode or not.
    arch : str
        The architecture (i.e x86_64) to use.
    or_update: bool, optional
        If True and package is installed, it will get updated instead.
    """
    cmd = ["flatpak", "install", remote, package]
    if not interractive:
        cmd.append("--noninteractive")
    if or_update:
        cmd.append("--or-update")
    if no_deps:
        cmd.append("--no-deps")
    run_flatpak_command(cmd, installation, arch=arch)


def flatpak_date_to_datetime(date: str) -> datetime:
    format = "%Y-%m-%d %H:%M:%S %z"
    return datetime.strptime(date, format)


def installation_exists(name: str) -> bool:
    """Check if a given flatpak installation exists."""
    result = subprocess.run(
        ["flatpak", "--installation=" + name, "list"], capture_output=True
    )
    return result.returncode == 0


def installation_path(name: str) -> str:
    """Find the file path of an installation."""
    if name == "user":
        return os.path.expanduser("~") + "/.local/share/flatpak/"
    elif name == "system":
        return "/var/lib/flatpak/"

    flatpak_install_dir = "/etc/flatpak/installations.d/"
    install_confs = os.listdir(flatpak_install_dir)
    for config_file in install_confs:
        with open(flatpak_install_dir + config_file, mode="r") as file:
            content = file.read().split("\n")
            header = content[0]
            attributes = [line.split("=", 1) for line in content[1:] if "=" in line]
            attributes = dict(attributes)
            if name in header:
                return attributes["Path"]

    raise Exception(f"Path of installation {name} was not found.")


def pin_package_version(
    package: str, commit: str, installation: str, interactive: bool, mask: bool = False
):
    """Fix a locally installed flatpak to the specified commit.

    Parameters
    ----------
    package : str
        Name of the package.
    commit : str
        Commit to which the package will be downgraded/updated.
    installation : str
        Installation in which the package is installed.
    interactive : bool
        Run the command in interactive mode.
    mask: bool
        If true will mask the package to avoid further update.
    """
    cmd = [
        "flatpak",
        "update",
        package,
        "--commit=" + commit,
        "--no-deps",
        "--no-related",
    ]
    if not interactive:
        cmd.append("--noninteractive")

    run_flatpak_command(cmd, installation, may_need_root=True)

    # Masking seems to produce weird results, even thougth we remove every
    # patterns at the end I noticed that some packages couldn't be updated
    # anymore. I actually never achieve to reproduce this bug but I prefer
    # the actual solution anyway.
    if mask:
        mask_package(package, installation)


def mask_package(package: str, installation: str, un_mask=False):
    """Maks a locally installed flatpak to avoid include it in further updates.

    Parameters
    ----------
    package : str
        Name of the package.
    installation : str
        Installation in which the package is installed.
    un_mask: bool
        If true will un_mask the package.
    """
    cmd = ["flatpak", "mask", package]
    if un_mask:
        cmd.append("--remove")
    run_flatpak_command(cmd, installation=installation, may_need_root=True)


def flatpak_update(package: str, installation: str, interactive: bool):
    """Update a locally installed flatpak to the latest version.

    Parameters
    ----------
    package : str
        Name of the package.
    installation : str
        Installation in which the package is installed.
    interactive : bool
        Run the command in interactive mode.
    """
    cmd = ["flatpak", "update", package]
    if not interactive:
        cmd.append("--noninteractive")

    run_flatpak_command(cmd, installation, may_need_root=True)


def get_additional_deps(remote: str, package: str) -> str:
    """Get the link to the github repo, containing the manifest and some additional
    dependencies used for the build.

    Raises
    ------
    Exception if the remote is not supported.
    CalledProcessError if the repo does not exists.
    """
    if remote == "flathub":
        link = "github.com/flathub/" + package
    elif remote == "flathub-beta":
        link = "github.com/flathub/" + package
    else:
        raise Exception(f"Remote {remote} is not supported.")

    # Verify that repo exists
    cmd = ["git", "ls-remote", "https://null:null@" + link]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        raise GitNotFoundException(f"No git repository found for package: {package}")

    return "https://" + link


def find_flatpak_commit_for_date(
    remote: str, installation: str, package: str, date: datetime
) -> str:
    """Find the latest commit of a flatpak, at a certain date. This is used to estimate
    the commit used for certain dependencies where it is not dirrectly provided.

    Parameters
    ----------
    remote : str
        Name of the remote (i.e flathub)
    installation : str
        Installation in which the package is installed.
    package : str
        Name of the package.
    date : datetime
        Date at which the commit was the latest.
    """
    cmd = ["flatpak", "remote-info", remote, package, "--log"]
    output = run_flatpak_command(cmd, installation, capture_output=True)
    commits = output.split("\n\n")[1:]
    # We use the fact that --log return commits from the most recent to the oldest
    for commit in commits:
        commit = cmd_output_to_dict(commit)
        commit_date = flatpak_date_to_datetime(commit["Date"])
        if commit_date <= date:
            return commit["Commit"]

    raise Exception("No commit matching the date has been found.")


def rebuild(
    dir: str,
    installation: str,
    package: str,
    branch: str,
    arch: str,
    install: bool = False,
) -> dict[str, int | float]:
    """Rebuild a flatpak locally.

    Parameters
    ----------
    dir : str
        Path to where the build should be done.
    installation : str
        Installation to use for the different dependencies.
    package : str
        Name of the package to rebuild.
    branch : str
        Branch name (usefull because it is sometimes embedded in some file.)
    arch : str
        Architecture to use (i.e x86_64).
    install : bool, optional
        If True, will install the rebuild package in the given installation.

    Returns
    -------
    dict[str, int | float]
        It returns a dictionary containing the folowing statistics
        {
            "build_time": float
            "cache_size": int
            "git_size": int
            "dl_size": int
        }
    """
    manifest = find_build_manifest(os.listdir(dir), package)
    if manifest is None:
        manifest = find_manifest(os.listdir(dir))
        if manifest is None:
            raise Exception(
                "Could not find manifest (none or too many of them are present)"
            )

    extra_fb_args = ["--arch", arch]
    if arch == "x86_64":
        extra_fb_args.append("--bundle-sources")

    cmd = [
        "flatpak",
        "run",
        # We need to put twice this flag, here and after the command because the first time it will
        # be applied to the flatpak run command and later to org.flatpak.Builder
        flatpak_installation_flag(installation),
        "org.flatpak.Builder",
        "--disable-cache",
        "--force-clean",
        "build",
        manifest,
        "--download-only",
    ]
    run_flatpak_command(cmd, installation, cwd=dir)

    cache_size = sum(
        f.stat().st_size for f in Path(dir + "/.flatpak-builder").rglob("*")
    )
    git_size = sum(
        f.stat().st_size for f in Path(dir + "/.flatpak-builder/git").rglob("*")
    )
    dl_size = sum(
        f.stat().st_size for f in Path(dir + "/.flatpak-builder/downloads").rglob("*")
    )

    cmd = [
        "flatpak",
        "run",
        flatpak_installation_flag(installation),
        "org.flatpak.Builder",
        "--disable-cache",
        "--force-clean",
        "build",
        manifest,
        "--repo=repo",
        "--mirror-screenshots-url=https://dl.flathub.org/repo/screenshots",
        "--sandbox",
        "--default-branch=" + branch,
        *extra_fb_args,
        "--remove-tag=upstream-maintained",
        "--disable-download",
    ]
    if install:
        cmd.append("--install")

    # We don't really need a super precise computation of time
    before = time.time()
    run_flatpak_command(cmd, installation, may_need_root=install, cwd=dir)
    after = time.time()

    stats = {
        "build_time": after - before,
        "cache_size": cache_size,
        "git_size": git_size,
        "dl_size": dl_size,
    }

    return stats


def find_manifest(files: list[str]) -> str | None:
    """Find the manifest file (of the form manifest.json) in a series of file.
    This is the fromat in which we find the manifest when it comes from the remote.
    """
    manifests = [file for file in files if file == "manifest.json"]
    if len(manifests) > 1:
        return None
    return manifests[0]


def find_build_manifest(files: list[str], package: str) -> str | None:
    """Find the manifest file, it is always of the form package.yml/json/yaml.
    This is the format in whcih we find the manifest when it comes from the github repo.
    """
    manifests = [
        file
        for file in files
        if file == package + ".json"
        or file == package + ".yml"
        or file == package + ".yaml"
    ]
    if len(manifests) > 1:
        return None
    return manifests[0]


def parse_manifest(manifest_content: str) -> dict[str, str]:
    """Parse a json format manifest."""
    return json.loads(manifest_content)


def flatpak_package_path(
    installation: str, package: str, arch: str | None = None
) -> str:
    """Find the path to a locally installed package

    Parameters
    ----------
    installation : str
        The installation in which it will search the package.
    package : str
        The package to search for.
    arch : str, optional
        The architecture to use, in case you have multiple version of the packge installed, with different architecture.

    Returns
    -------
    str
        The path to the package.
    """
    cmd = ["flatpak", "info", "-l", package]
    if arch:
        cmd.append("--arch=" + arch)
    flatpak_info = run_flatpak_command(cmd, installation, capture_output=True)
    return flatpak_info.strip()


def find_time_in_binary(path: str) -> list[datetime]:
    """This, with find_closest_time, is an attempt to automatically find binary embedded timestamps.
    It does not work really well, so for now it is unused and I manually check for these timestamps if needed.
    """
    cmd = ["strings", path]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        return []
    output = result.stdout.decode("UTF-8").splitlines()
    fulldate_pattern = re.compile(r"\w{3} \d\d \d{4} \d\d:\d\d:\d\d")
    dates: list[datetime] = []
    for str in output:
        match = fulldate_pattern.search(str)
        if match:
            date = match.group()
            date = datetime.strptime(date, "%b %d %Y %H:%M:%S")
            date = date.replace(tzinfo=timezone.utc)
            dates.append(date)

    return dates


def find_closest_time(flatpak_package_path: str, estimate: datetime) -> datetime:
    """Find timestamps in binary which are the closest to the first estimate."""
    times: list[datetime] = []
    for root, _, files in os.walk(flatpak_package_path + "/files/"):
        for file in files:
            times.extend(find_time_in_binary(os.path.join(root, file)))

    if len(times) > 0:
        return times[
            min(
                range(len(times)),
                key=lambda t: abs(estimate.timestamp() - times[t].timestamp()),
            )
        ]

    return estimate


def ostree_checkout(repo: str, ref: str, dest: str, root=False):
    """Perform an ostree checkout, namely, take the content of a commit and put it in a folder.
    It uses the -U flag, which does not change file ownership and ignore x-attributes.

    Parameters
    ----------
    repo : str
        Path to the ostree repo.
    ref : str
        Name of the branch in the ostree repo.
    dest : str
        Path to the destination folder.
    root : bool, optional.
        If true, will run the command with sudo (needed depending on how the ostree is configured.)
    """
    cmd = ["ostree", "checkout", ref, dest, "--repo=" + repo, "-U"]
    if root:
        cmd.insert(0, "sudo")

    subprocess.run(cmd).check_returncode()


def run_diffoscope(
    original_path: str, rebuild_path: str, html_output: str | None = None
) -> int:
    """Run diffoscope with the --exclude-directory-metadata flag set to yes. This ignores metadatas, such as timestamps."""
    cmd = [
        "diffoscope",
        original_path,
        rebuild_path,
        "--exclude-directory-metadata=yes",
    ]
    if html_output:
        cmd.append("--html=" + html_output)

    return subprocess.run(cmd).returncode

def strip_non_determinism(path: str):
    """Run strip-nondeterminism on everyfiles inside path."""
    for root, _, files in os.walk(path):
        for f in files:
            path = os.path.join(root, f)
            if os.path.exists(path):
                cmd = ['strip-nondeterminism', path]
                subprocess.run(cmd)

def flatpak_uninstall(
    package: str, installation: str, interactive: bool, arch: str, force: bool = False
):
    """uninstall a locally installed flatpak."""
    cmd = ["flatpak", "uninstall", package]
    if force:
        cmd.append("--force-remove")
    run_flatpak_command(cmd, installation, interactive=interactive, arch=arch)


def get_default_arch() -> str:
    """Returns the default flatpak architecture of the system (most likely x86_64)."""
    cmd = ["flatpak", "--default-arch"]

    result = subprocess.run(cmd, capture_output=True)
    result.check_returncode()

    return result.stdout.decode("UTF-8").strip()


def is_arch_available(arch: str) -> bool:
    """Returns true if the following arch is available on the system."""
    cmd = ["flatpak", "--supported-arches"]
    result = subprocess.run(cmd, capture_output=True)
    result.check_returncode()

    available = result.stdout.decode("UTF-8").split("\n")
    return arch in available


def flatpak_remote_add(
    remote: str, installation: str, url: str, gpg_import: str | None = None
):
    """Add a remote to the installation."""
    cmd = ["flatpak", "remote-add", "--if-not-exists", remote, url]
    if gpg_import:
        cmd.append("--gpg-import=" + gpg_import)

    run_flatpak_command(cmd, installation, may_need_root=True)


def flatpak_remote_modify_url(remote: str, installation: str, url: str):
    """Modify a remote to make it point to the given url."""
    cmd = ["flatpak", "remote-modify", "--url=" + url, remote]

    run_flatpak_command(cmd, installation, may_need_root=True)


def ostree_init(repo: str, mode: str, path: str):
    cmd = ["ostree", "--repo=" + repo, "--mode=" + mode, "init"]
    subprocess.run(cmd, cwd=path).check_returncode()


def generate_deltas(repo_dir: str, repo: str):
    """One of the last step done by the flathub buildbot, I have no idea what it does, but to be as consistent as
    possible, we also do it here.
    """
    cmd = (
        "flatpak build-update-repo --generate-static-deltas --static-delta-ignore-ref=*.Debug --static-delta-ignore-ref=*.Sources "
        + repo
    )
    subprocess.run(cmd, cwd=repo_dir, shell=True).check_returncode()


def flatpak_install_deps(
    remote: str,
    installation: str,
    arch: str,
    manifest_path: str,
    beta_remote: str | None = None,
) -> list[str]:
    """Install the needed flatpak deps (runtime, sdk, skd-extension, etc.) specified in the manifest.
    Since flatpak-builder does the parsing of the metadatas to figure out which branch to use for each extensions,
    we return this information back to the caller.
    """
    cmd = [
        "flatpak",
        "run",
        "org.flatpak.Builder",
        "build",
        manifest_path,
        "--install-deps-from=" + remote,
        "--install-deps-only",
    ]
    if beta_remote:
        cmd.append("--install-deps-from=" + beta_remote)

    output = run_flatpak_command(cmd, installation, arch=arch, capture_output=True)
    extensions = list()
    for line in output.split("\n"):
        if line.startswith("Dependency Extension"):
            line = line.split(":")[1].split(" ")[1:]
            extensions.append(f"{line[0]}/{arch}/{line[1]}")

    return extensions


def compute_folder_hash(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    # My previous hash method seemed to work, but in case there are soft-links, this one
    # should be more robust (I hope).
    return dirhash(path, "sha1", followlinks=True)


def compute_folder_bin_hash(path: str) -> str | None:
    """Computes the hash of a folder, while only considering non
    text files (images, archives, compiled programs, ...)
    """
    if not os.path.exists(path):
        return None

    # Forgive Me Father For I Have Sinned
    cmd = f"find {path} -type f -exec grep --null -IL . {{}} \; | LC_ALL=C sort -z | xargs -0 sha1sum | sed 's/\s.*$//' | sha1sum | sed 's/\s.*$//'"

    result = subprocess.run(cmd, capture_output=True, shell=True)

    if result.returncode != 0:
        return None
    # Remove the last /n
    return result.stdout.decode("UTF-8").strip()


def compute_folder_elf_hash(path: str) -> str | None:
    """Computes the hash of a folder, while only considering elf files."""
    if not os.path.exists(path):
        return None

    cmd = f"find {path} -exec file {{}} \; | grep -i elf | cut -d: -f1 | LC_ALL=C sort -z | xargs sha1sum | sed 's/\s.*$//' | sha1sum | sed 's/\s.*$//'"

    result = subprocess.run(cmd, capture_output=True, shell=True)

    if result.returncode != 0:
        return None
    return result.stdout.decode("UTF-8").strip()


def compute_repro_score(original: str, rebuild: str) -> tuple[int, int, float] | None:
    cmd = f"diff -rq {original} {rebuild} --no-dereference | wc -l"
    count_cmd = f"find {original} -type f | wc -l"
    result_diff = subprocess.run(cmd, capture_output=True, shell=True)
    result_count = subprocess.run(count_cmd, capture_output=True, shell=True)

    if result_diff.returncode != 0 or result_count.returncode != 0:
        return None

    result_diff = int(result_diff.stdout.decode("UTF-8").strip())
    result_count = int(result_count.stdout.decode("UTF-8").strip())

    # We define a first scoring methond ~= #of good files/#of files
    score = (result_count - result_diff) / result_count
    return (result_diff, result_count, score)


def flatpak_ref_full_name(ref: str, arch: str, branch: str) -> str:
    """Convert a ref into it's full name. It turns out 'package' and
    'package/arch/branch' are both valid ids in certain context (for instance
    the sdk in the manifest), this function automatically converts it into the
    full form a/b/c if needed.
    """
    splited = ref.split("/")
    if len(splited) >= 3:
        return ref
    else:
        return f"{ref}/{arch}/{branch}"


def check_program_version(
    remote: str,
    package: str,
    installation: str,
    expected_commit: str,
    arch: str,
    try_to_solve: bool = False,
) -> bool:
    """Assert that the active commit of a local package is expected_commit.
    If try_to_solve is True, it will try to force the program to be at the expected_commit.
    """
    infos = flatpak_info(installation, package)
    commit = infos.get("Commit")
    if commit:
        is_same = commit == expected_commit
    else:
        # Risky route, if there is an active and latest commit, this probably mean something weird happened.
        active_commit = infos.get("Active commit")
        is_same = active_commit == expected_commit

    if not is_same and try_to_solve:
        flatpak_uninstall(
            package, installation, interactive=False, arch=arch, force=True
        )
        flatpak_install(
            remote, package, installation, interractive=False, arch=arch, no_deps=True
        )
        pin_package_version(package, expected_commit, installation, interactive=False)
        return check_program_version(
            remote, package, installation, expected_commit, arch, try_to_solve=False
        )
    else:
        return is_same


def main():
    args = parse_args()
    package = args.flatpak_name
    user_install = args.user
    system_install = args.system
    custom_installation = args.installation
    interactive = args.interactive
    commit = args.commit
    time = args.time
    arch = args.arch
    branch = args.branch
    beta = args.beta
    remote = "flathub" if not beta else "flathub-beta"
    diffoscope = args.diffoscope
    strip = args.strip


    # Make sure to avoid creating path issues. (It should not be needed actually)
    package_path_name = package.replace("/", "_")
    # Keep a few stats to analyse later on.
    statistics = {
        "name": package,
        "build_sucess": False,
        "is_reproducible": False,
        "use_fixed_time": time != None,
    }

    if user_install:
        installation = "user"
    elif system_install:
        installation = "system"
    elif custom_installation:
        installation = custom_installation
        if not installation_exists(installation):
            raise Exception(f"Installation {installation} does not exist.")
    else:
        installation = "user"

    # Add flathub and flathub-beta as remotes
    flatpak_remote_add(
        "flathub", installation, "https://flathub.org/repo/flathub.flatpakrepo"
    )
    # Make sure the name of the remote is flathub
    flatpak_remote_modify_url("flathub", installation, "https://flathub.org/repo/")
    # Same but with flathub beta
    flatpak_remote_add(
        "flathub-beta",
        installation,
        "https://flathub.org/beta-repo/flathub-beta.flatpakrepo",
    )
    flatpak_remote_modify_url(
        "flathub-beta",
        installation,
        "https://flathub.org/beta-repo/flathub-beta.flatpakrepo",
    )

    if arch is None:
        arch = get_default_arch()
    elif not is_arch_available(arch):
        raise Exception(
            f"Cannot build, because {arch} is not an available architecture on your system."
        )

    available_branches = get_available_branches(remote, installation, package, arch)
    if branch is not None and branch not in available_branches:
        raise Exception(
            f"Cannot rebuild using branch: {branch}, because it does not exist."
        )
    if branch is None:
        branch = available_branches[0]

    git_url = get_additional_deps(remote, package)

    full_package_id = f"{package}/{arch}/{branch}"
    flatpak_install(
        remote, full_package_id, installation, interactive, arch, or_update=True
    )

    if commit:
        pin_package_version(full_package_id, commit, installation, interactive)

    metadatas = flatpak_info(installation, full_package_id)

    # Sanity check
    assert metadatas["Branch"] == branch
    if commit:
        assert metadatas["Commit"] == commit

    statistics["commit"] = metadatas["Commit"]
    statistics["branch"] = branch

    original_path = flatpak_package_path(installation, full_package_id)

    if time:
        build_time = flatpak_date_to_datetime(time)
    elif args.estimate_time:
        build_time_estimate = flatpak_date_to_datetime(metadatas["Date"])
        build_time = find_closest_time(original_path, build_time_estimate)
    else:
        build_time = flatpak_date_to_datetime(metadatas["Date"])

    build_timestamp = build_time.timestamp()

    statistics["time_of_rebuild"] = str(build_time)
    statistics["timestamp_of_rebuild"] = build_timestamp

    # Init the build directory
    dir = package_path_name
    os.mkdir(dir)
    path = f"{os.curdir}/{dir}"
    if beta:
        repo = Repo.clone_from(git_url, path, branch="beta")
    else:
        repo = Repo.clone_from(git_url, path)
        # Okay this part sucks, but the default branch isn't always the right one
        # we therefore need to be careful (e.g ar.xjuan.Cambalache)
        remote_refs = repo.remote().refs
        # In case we want a specific flathub branch, it generally means this branch will
        # also exist with the same name or at least will end with the same name.
        possible_ones = [ref for ref in remote_refs if ref.name.endswith(branch)]
        if len(possible_ones) > 0:
            git_ref = possible_ones[0]
            git_ref.checkout()
        else:
            possible_ones = [ref for ref in remote_refs if ref.name.endswith("master")]
            if len(possible_ones) > 0:
                git_ref = possible_ones[0]
                git_ref.checkout()
            # Otherwise we just use the default branch and hope it is the right one

    # Last commit isn't always the one corresponding to what's on flathub
    # In particular some people merge things that don't even build on master (or the build fail but they don't try it again)
    for c in repo.iter_commits():
        if c.committed_datetime < build_time:
            print(f"Has chosen git commit {c.name_rev}")
            repo.git.checkout(c)
            statistics["git-commit"] = c.name_rev
            break
    repo.submodule_update()

    # Keep track of flatpak deps hashes (runtime, sdk, sdk-extension and base app)
    statistics["flatpak-deps"] = dict()

    full_builder_name = f"{FLATPAK_BUILDER}/{arch}/stable"
    flatpak_install("flathub", full_builder_name, installation, interactive, arch)

    manifest_path = f"{original_path}/files/manifest.json"
    with open(manifest_path, mode="r") as manifest:
        manifest_content = manifest.read()
        manifest = parse_manifest(manifest_content)

    sdk_extensions = flatpak_install_deps(
        "flathub", installation, arch, manifest_path, remote
    )

    ostree_init("repo", mode="archive-z2", path=path)

    # Change time of manifests files
    for root, _, files in os.walk(path):
        for file in files:
            # Try to only touch manifest files
            if file.endswith(".json") or file.endswith(".yml"):
                os.utime(os.path.join(root, file), (build_timestamp, build_timestamp))

    original_artifact = package_path_name + ".original"
    rebuild_artifact = package_path_name + ".rebuild"
    report = package_path_name + ".report.html"

    base_app = manifest.get("base")
    base_app_got_well_downgraded = True
    if base_app != None:
        base_version = manifest["base-version"]
        full_name = flatpak_ref_full_name(base_app, arch, base_version)
        flatpak_install(remote, full_name, installation, interactive, arch)
        base_app_commit = find_flatpak_commit_for_date(
            remote, installation, full_name, build_time
        )
        pin_package_version(full_name, base_app_commit, installation, interactive)
        base_app_got_well_downgraded = check_program_version(
            remote, full_name, installation, base_app_commit, arch, try_to_solve=True
        )
        statistics["flatpak-deps"][full_name] = base_app_commit
        base_extensions = manifest.get("base-extensions")
        if base_extensions:
            for extension in base_extensions:
                extension_full_name = flatpak_ref_full_name(
                    extension, arch, base_version
                )
                flatpak_install(
                    remote, extension_full_name, installation, interactive, arch
                )
                base_app_extension_commit = find_flatpak_commit_for_date(
                    remote, installation, extension_full_name, build_time
                )
                pin_package_version(
                    extension_full_name,
                    base_app_extension_commit,
                    installation,
                    interactive,
                )
                base_app_got_well_downgraded = check_program_version(
                    remote,
                    full_name,
                    installation,
                    base_app_commit,
                    arch,
                    try_to_solve=True,
                )
                statistics["flatpak-deps"][full_name] = base_app_commit
                base_extensions = manifest.get("base-extensions")

    sdk_extension_commit = list()
    for sdk_extension in sdk_extensions:
        extension_commit = find_flatpak_commit_for_date(
            remote, installation, sdk_extension, build_time
        )
        pin_package_version(sdk_extension, extension_commit, installation, interactive)
        sdk_extension_commit.append((sdk_extension, extension_commit))
        statistics["flatpak-deps"][sdk_extension] = extension_commit

    builder_commit = find_flatpak_commit_for_date(
        remote, installation, full_builder_name, build_time
    )
    pin_package_version(full_builder_name, builder_commit, installation, interactive)
    statistics["flatpak-deps"][full_builder_name] = builder_commit

    install_path = installation_path(installation)
    ostree_checkout(
        f"{install_path}/repo",
        metadatas["Ref"],
        original_artifact,
        root=(installation != "user"),
    )

    sdk_full_name = flatpak_ref_full_name(
        manifest["sdk"], arch, manifest["runtime-version"]
    )
    sdk_commit = manifest["sdk-commit"]
    pin_package_version(sdk_full_name, sdk_commit, installation, interactive)

    var = manifest.get("var")
    if var:
        var_commit = find_flatpak_commit_for_date(remote, installation, var, build_time)
        pin_package_version(var, var_commit, installation, interactive)

    statistics["flatpak-deps"][sdk_full_name] = sdk_commit

    runtime_full_name = flatpak_ref_full_name(
        manifest["runtime"], arch, manifest["runtime-version"]
    )
    runtime_commit = manifest["runtime-commit"]
    # A bit overkill but that ensures everything is the same
    pin_package_version(runtime_full_name, runtime_commit, installation, interactive)

    statistics["flatpak-deps"][runtime_full_name] = runtime_commit

    sdk_got_well_downgraded = list()
    for extension, commit in sdk_extension_commit:
        sdk_got_well_downgraded.append(
            check_program_version(
                remote,
                extension,
                installation,
                commit,
                arch,
                try_to_solve=True,
            )
        )
    # Make sure we downgraded things correctly (as you can guess this was not always the case hence the check)
    if not (
        check_program_version(
            remote,
            sdk_full_name,
            installation,
            manifest["sdk-commit"],
            arch,
            try_to_solve=True,
        )
        and check_program_version(
            remote,
            runtime_full_name,
            installation,
            manifest["runtime-commit"],
            arch,
            try_to_solve=True,
        )
        and check_program_version(
            remote,
            FLATPAK_BUILDER,
            installation,
            builder_commit,
            arch,
            try_to_solve=True,
        )
        and (not False in sdk_got_well_downgraded)
        and base_app_got_well_downgraded
    ):
        statistics["wrong_deps_detected"] = True
        statistics = json.dumps(statistics, indent=4)
        with open(f"{path}/{package_path_name}.stats.json", "w") as f:
            f.write(statistics)
        shutil.move(original_artifact, f"{path}/{original_artifact}")
        raise Exception()

    try:
        build_stats = rebuild(
            path, installation, package, metadatas["Branch"], arch, install=False
        )
        statistics.update(build_stats)
    except:
        statistics = json.dumps(statistics, indent=4)
        with open(f"{path}/{package_path_name}.stats.json", "w") as f:
            f.write(statistics)
        shutil.move(original_artifact, f"{path}/{original_artifact}")
        raise

    statistics["build_sucess"] = True

    generate_deltas(path, "repo")

    ostree_checkout(
        f"{path}/repo",
        metadatas["Ref"],
        rebuild_artifact,
        root=(installation != "user"),
    )

    # Clean up
    flatpak_uninstall(full_package_id, installation, interactive, arch)

    if strip:
        strip_non_determinism(original_artifact)
        strip_non_determinism(rebuild_artifact)

    if diffoscope:
        diffoscope_result = run_diffoscope(original_artifact, rebuild_artifact, report)
        # Report is only created when build is not reproducible
        if diffoscope_result != 0:
            # Sometimes diffoscope fails, cool
            if os.path.exists(report):
                shutil.move(report, f"{path}/{report}")
            else:
                statistics["diffoscope_failed"] = True

    original_hash = compute_folder_hash(original_artifact)
    rebuild_hash = compute_folder_hash(rebuild_artifact)
    reproducible = original_hash == rebuild_hash

    original_bin_hash = compute_folder_bin_hash(original_artifact)
    rebuild_bin_hash = compute_folder_bin_hash(rebuild_artifact)

    original_elf_hash = compute_folder_elf_hash(original_artifact)
    rebuild_elf_hash = compute_folder_elf_hash(rebuild_artifact)

    bin_reproducible = original_bin_hash == rebuild_bin_hash
    elf_reproducible = original_elf_hash == rebuild_elf_hash

    repro_score = compute_repro_score(original_artifact, rebuild_artifact)
    if repro_score is not None:
        bad_files, total_files, repro_score = repro_score
        statistics["bad_files"] = bad_files
        statistics["total_files"] = total_files
        statistics["repro_score"] = repro_score

    statistics["original_hash"] = original_hash
    statistics["rebuild_hash"] = rebuild_hash
    statistics["original_bin_hash"] = original_bin_hash
    statistics["rebuild_bin_hash"] = rebuild_bin_hash
    statistics["original_elf_hash"] = original_elf_hash
    statistics["rebuild_elf_hash"] = rebuild_elf_hash

    statistics["is_reproducible"] = reproducible
    statistics["is_bin_reproducible"] = bin_reproducible
    statistics["is_elf_reproducible"] = elf_reproducible

    # Make sure we only leave one directory
    shutil.move(original_artifact, f"{path}/{original_artifact}")
    shutil.move(rebuild_artifact, f"{path}/{rebuild_artifact}")

    statistics = json.dumps(statistics, indent=4)
    with open(f"{path}/{package_path_name}.stats.json", "w") as f:
        f.write(statistics)

    if reproducible:
        print(f"{package} is reproducible.")
    else:
        print(
            f"{package} is not reproducible, rebuild hash = {rebuild_hash}, original hash = {original_hash}."
        )
    sys.exit(not reproducible)


if __name__ == "__main__":
    main()
