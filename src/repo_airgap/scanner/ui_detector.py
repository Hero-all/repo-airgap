VIEW_SUFFIXES = {".tsx", ".jsx", ".vue", ".svelte", ".html"}


def ui_matches(path, line, keywords):
    if path.suffix.lower() not in VIEW_SUFFIXES:
        return []
    return [word for word in keywords if word.casefold() in line.casefold()]
