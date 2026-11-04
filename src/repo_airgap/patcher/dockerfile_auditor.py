import re

FLAGS = re.compile(r"\b(?:\w*(?:TELEMETRY|ANALYTICS|UPDATE_CHECK|PROMO_URL|CLOUD_API)\w*)\b", re.I)


def config_match(path, line):
    name = path.name.lower()
    is_config = (
        name.startswith(".env")
        or name.startswith("dockerfile")
        or name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
    )
    return is_config and FLAGS.search(line)
