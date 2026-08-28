"""Bronze collector: raw daily snapshots of OpenRouter and HuggingFace.

Nothing here parses, filters, joins or interprets. Bytes as received,
gzipped, plus a manifest recording what was asked for and what came back.
"""

__version__ = "0.1.0"
