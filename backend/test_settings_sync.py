"""Settings sync test: every consumer must follow settings changes after hot-reload."""
import os
import sys

sys.path.insert(0, ".")

from app.config import get_settings

# Simulate user saving new names in settings page (env only, .env untouched)
os.environ["ZEN_MODEL"] = "test-zen-model-xyz"
os.environ["NLI_GROQ_MODEL"] = "test-nli-model-xyz"
os.environ["CLAIM_EXTRACTION_MODEL"] = "test-extract-model-xyz"
get_settings.cache_clear()

from app.api.settings import _reload_runtime
from app.utils.llm_clients import _effective_model

s = get_settings()
# 1. Dropdown display name follows ZEN_MODEL
zen_entry = s.supported_models["mimo-v2.5"]
assert "test-zen-model-xyz" in zen_entry["name"], zen_entry
print("dropdown name:", zen_entry["name"])
# 2. Outbound gateway call follows ZEN_MODEL
assert _effective_model("zen", "mimo-v2.5") == "test-zen-model-xyz"
assert _effective_model("groq", "openai/gpt-oss-20b") == "openai/gpt-oss-20b"
print("gateway mapping OK")
# 3. Fresh singletons pick up new names
from app.core.claim_extractor import get_claim_extractor
from app.core.claim_adjudicator import get_claim_adjudicator
from app.core.nli_model import get_nli_model

assert get_claim_extractor().model_name == "test-extract-model-xyz"
assert get_claim_adjudicator().model == "test-zen-model-xyz"
assert get_nli_model().groq_model == "test-nli-model-xyz"
print("fresh singletons OK")

# 4. Hot-reload resets ALL key/model-holding singletons
import app.utils.llm_clients as llm_mod
import app.core.claim_extractor as ext_mod
import app.core.claim_adjudicator as adj_mod
import app.core.nli_model as nli_mod
import app.core.domain_source_router as router_mod
import app.core.verifier as verifier_mod
import app.core.web_search as ws_mod
from app.core.domain_source_router import get_domain_source_router
from app.core.verifier import get_claim_verifier
from app.core.web_search import get_web_searcher

get_claim_verifier(); get_domain_source_router(); get_web_searcher()
llm_mod.get_llm_client()
_reload_runtime()
for mod, var in [(llm_mod, "_llm_client"), (ext_mod, "_extractor"),
                 (adj_mod, "_adjudicator"), (nli_mod, "_nli_model"),
                 (router_mod, "_router"), (verifier_mod, "_verifier"),
                 (ws_mod, "_searcher")]:
    assert getattr(mod, var) is None, f"{mod.__name__}.{var} not reset!"
print("all 7 singletons reset OK")

# effective summary shows new models
from app.api.settings import _effective_summary
eff = _effective_summary()
assert eff["claim_model"] == "test-extract-model-xyz", eff
assert eff["nli_model"] == "test-nli-model-xyz", eff
print("effective summary OK:", eff["claim_model"], "/", eff["nli_model"])

# Restore
for k in ["ZEN_MODEL", "NLI_GROQ_MODEL", "CLAIM_EXTRACTION_MODEL"]:
    os.environ.pop(k, None)
get_settings.cache_clear()
_reload_runtime()
print("SETTINGS-SYNC PASS")
