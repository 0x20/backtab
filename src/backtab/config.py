# Config values

import typing
import os.path


def get_path(object, *args, default=None):
    for arg in args[:-1]:
        object = object.get(arg, {})
    return object.get(args[-1], default)


class ConfigData:
    DATA_DIR: str = os.path.join(os.getcwd(), "mut_data")
    PORT: int = 80
    LISTEN_ADDR: str = "localhost"
    SLOWDOWN: float = 0.1
    EVENT_MODE: bool = False

    def load_from_config(self, configPath: str):
        import yaml
        with open(configPath, "rt") as configFile:
            config = yaml.load(configFile, Loader=yaml.SafeLoader)
        self.DATA_DIR = get_path(config, "datadir", default=self.DATA_DIR)
        self.PORT = get_path(config, "http", "port", default=self.PORT)
        self.LISTEN_ADDR = get_path(config, "http", "listen", default=self.LISTEN_ADDR)
        self.SLOWDOWN = get_path(config, "slowdown", default=self.SLOWDOWN)
        self.EVENT_MODE = get_path(config, "event_mode", default=self.EVENT_MODE)

        print("Config:\n"
              "  DATA_DIR: %(DATA_DIR)s\n"
              "  PORT: %(PORT)s\n"
              "  LISTEN_ADDR: %(LISTEN_ADDR)s\n"
              "  SLOWDOWN: %(SLOWDOWN)s\n"
              "  EVENT_MODE: %(EVENT_MODE)s\n" % dict(
            DATA_DIR=self.DATA_DIR,
            PORT=self.PORT,
            LISTEN_ADDR=self.LISTEN_ADDR,
            SLOWDOWN=self.SLOWDOWN,
            EVENT_MODE=self.EVENT_MODE,
        ))

SERVER_CONFIG = ConfigData()
