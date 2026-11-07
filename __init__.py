"""us-air-rules: Title 40 air-rule screening for Hermes Agent.

Read-only lookups against www.ecfr.gov and www.federalregister.gov, plus two bundled
applicability packs (plant and household). No API key. The plugin returns rule text and
a screening verdict, not a compliance determination and not legal advice.
"""

import logging
from pathlib import Path

from . import schemas
from .tools import Tools, cli_warm, make_handlers, slash_usair

logger = logging.getLogger(__name__)

_SKILL = Path(__file__).resolve().parent / "skills" / "air-rules" / "SKILL.md"
_SKILL_DESCRIPTION = (
    "Does a US EPA air rule apply (plant, kiln, boiler, engine, wood stove, paint, burning)? "
    "Use the usair_ tools and quote source, not memory."
)


def register(ctx):
    """Register the six tools, the /usair slash command, the warm CLI, and the quoting skill."""

    def setting(key, default=None):
        try:
            return ctx.get_config(key, default)
        except Exception:
            return default

    handlers = make_handlers(Tools(store=_store(setting)))
    for schema in schemas.ALL_SCHEMAS:
        ctx.register_tool(
            name=schema["name"],
            toolset="us-air-rules",
            schema=schema,
            handler=handlers[schema["name"]],
            emoji="🏭",
        )
    try:
        ctx.register_command("usair", slash_usair, description="Demo, template, or list the fictional air-rule profiles")
    except Exception:
        logger.warning("us-air-rules slash command was not registered", exc_info=True)
    try:
        ctx.register_cli_command(
            name="us-air-rules",
            help="Warm the Title 40 cache",
            setup_fn=_setup_cli,
            handler_fn=_run_cli,
        )
    except Exception:
        logger.warning("us-air-rules CLI command was not registered", exc_info=True)
    try:
        ctx.register_skill("air-rules", _SKILL, description=_SKILL_DESCRIPTION)
    except Exception:
        logger.warning("us-air-rules skill was not registered", exc_info=True)


def _store(setting):
    from .client import Store
    return Store(settings=setting)


def _setup_cli(subparser):
    subs = subparser.add_subparsers(dest="us_air_rules_command")
    subs.add_parser("warm", help="Fetch the current Title 40 date into the cache")
    subparser.set_defaults(func=_run_cli)


def _run_cli(args):
    cmd = getattr(args, "us_air_rules_command", None)
    if cmd == "warm":
        try:
            print(cli_warm())
        except Exception as err:
            print(f"us-air-rules warm failed: {err}")
    else:
        print("Usage: hermes us-air-rules warm")
