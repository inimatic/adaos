"""
NLU runtime glue for AdaOS.

Importing this package is enough to register all NLU-related event
subscriptions (see dispatcher.py). The actual NLU engine lives
outside the hub; it publishes ``nlp.intent.detected`` events that
are then mapped to scenario/skill actions here.
"""

from . import dispatcher as _dispatcher  # noqa: F401

