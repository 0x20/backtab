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

    def load_from_config(self, configPath: str):
        import yaml
        with open(configPath, "rt") as configFile:
            config = yaml.load(configFile)
        self.DATA_DIR = get_path(config, "datadir", default=self.DATA_DIR)
        self.PORT = get_path(config, "http", "port", default=self.PORT)
        self.LISTEN_ADDR = get_path(config, "http", "listen", default=self.LISTEN_ADDR)

SERVER_CONFIG = ConfigData()