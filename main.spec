# Compatibility entry: keep both build commands on the same configuration.
from pathlib import Path
canonical = Path(SPECPATH) / 'Bloom Traveler.spec'
exec(compile(canonical.read_text(encoding='utf-8'), str(canonical), 'exec'))
