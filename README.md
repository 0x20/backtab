Backtab
=======

This is the backend service for [tab-ui](https://github.com/0x20/tab-ui). The current recommended way to run this is Docker.

Running on Docker
-----------------

Build the docker image using

    docker build -t backtab .

Then, create the docker VM using:

    docker create \
        -v backtab:/srv/backtab \
        -p 4903:4903 \
        -e TAB_DATA_REPO=git@github.com:0x20/tab-data \
        -e TEST_MODE=1 \
        --name backtab \
        --init \
        backtab:latest

You'll almost certainly want to change the repo name, and for production
use, you'll want to remove the TEST_MODE environment variable.

Once the container is created, you'll need to copy in an SSH private key
that has push access to the remote repo. (For test mode, you can use HTTP
and skip this step, or you can simply use an SSH key that only has read
access):

    docker cp /path/to/id_rsa backtab:/root/.ssh/

Finally, you can start the backend:

    docker start backtab

Running natively (on Debian) (deprecated)
-----------------------------------------

*This method is deprecated and the documentation is kept for historical purposes*

Build a package using

    dpkg-buildpackage --no-sign

Then, install it using

    sudo dpkg -i ../backtab_1.1_all.deb

The systemd init script will likely fail to start if you configured
backtab to use a ssh:// url; if so, add your SSH private key to
`/var/lib/backtab/.ssh` and then backtab should start.

Running natively
----------------

Check out a copy of your data repository wherever you find convenient.
We'll call that location `/srv/backtab/tab-data`.

Next, you'll need to create a virtualenv and install backtab:

    python3 -mvenv /path/to/backtab.venv
    . /path/to/backtab.venv/bin/activate
    pip install .

Next, copy config.yml to somewhere convenient; in production I usually call
it `backtab.yml`. Edit it to your taste.

Finally, create a systemd unit for backtab:

    [Unit]
    Description=Tab backend
    Wants=network.target
    After=network.target

    [Service]
    User=hsg
    Type=notify
    NotifyAccess=main
    ExecStart=/path/to/backtab.venv/bin/backtab-server -c /path/to/backtab.yml
    StandardOutput=journal

    [Install]
    WantedBy=multi-user.target

Finally, enable and start it:

    systemctl enable backtab.service
    systemctl start backtab.service
