
<h1 align="center">
  <br>
  Flatpak Rebuilder
  <br>
</h1>

<h4 align="center">A CLI tool to verify a flatpak locally by using <a href="https://reproducible-builds.org/">reproducible builds</a>.</h4>

<p align="center">
  <a href="#key-features">Key Features</a> •
  <a href="#installation">Installation</a> •
  <a href="#usage">Usage</a>
</p>


## Key Features

* Recreate a flatpak in the same way you install one.
* Support custom flatpak installation, to avoid breaking your main install.
* Support custom remotes other than flathub (untested for now).

## Installation

This program is built using the [poetry](https://github.com/python-poetry/poetry) python dependency management tool.
Once installed you simply clone the repo and run the following:
```bash

# Install dependencies
$ poetry install

# Run
$ poetry run flatpak-rebuilder <remote> <package>
```

You can also use `poetry shell` to spawn a shell in the local install and run the commands without `poetry run`.

It is highly recommended to use this tool with a custom installation, here is how you can create one.
Make sure this directory exist:
```bash
$ sudo mkdir -p /etc/flatpak/installations.d
```
Each custom installation has it's own config file ending in `.conf` in this directory.
You need to create one with root privileges, for instance with the following content:
```bash
$ cat /etc/flatpak/installations.d/rebuilder.conf
[Installation "rebuilder"]
Path=/home/<username>/flatpak-rebuilder-install/
DisplayName=Flatpak rebuilder installation
StorageType=harddisk
```
The above installation will be located in `~/flatpak-rebuilder-install/`. 
See the [flatpak documentation](https://docs.flatpak.org/en/latest/flatpak-command-reference.html#flatpak-installation)
or their [tips and tricks](https://docs.flatpak.org/en/latest/tips-and-tricks.html) page to learn more.

Don't forget to setup the remotes you want to use with this new installation. If you want to add `flathub` for instance,
you need to do the following (if your install is called rebuilder):
```bash
$ flatpak remote-add flathub https://flathub.org/repo/flathub.flatpakrepo --installation=rebuilder
```

## Usage
It works in the same way `flatpak install` does, namely by providing the name of a remote followed by the name of the package to rebuild.
The following options are valid:
* `--commit=COMMIT` The commit number of the package to rebuild, if you want to rebuild an older version.
* `--installation=INSTALLATION` The name of the flatpak installation to use.
* `--interractive` If set, will run the commands without the `--noninteractive` flag, which will ask you if you want to install the dependencies.

Here is an example:
```bash
$ flatpak-rebuilder flathub org.gnome.Dictionary --installation=rebuilder --interactive
```
This will create the `org.gnome.Dictionary` directory, with `build` and `repo` sub-directories that contains the rebuild
and an ostree repo of the rebuild.

Be aware that this requires root privileges at certain moment in order to downgrade packages.

## Roadmap

This roadmap is more for me to not forget what I should do and fix.

- [ ] Use the absolute name of the remote, rather than the local one
- [ ] Add step to generate comparable artifacts
- [ ] Add comparison of these artifacts with diffoscope support
- [ ] See if sdk-extensions versions may change the output.
- [ ] Add a signing/verification mechanism, probably using [in-toto](https://in-toto.io/).
