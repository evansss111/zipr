"""Council configuration — seeds, models, timing."""

import os

# Claude models per agent (Haiku for speed/cost, Sonnet for synthesis)
MODEL_HERALD = "claude-haiku-4-5-20251001"
MODEL_KRONOS = "claude-haiku-4-5-20251001"
MODEL_LOOM   = "claude-sonnet-4-6"
MODEL_VERITY = "claude-sonnet-4-6"
MODEL_CODEX  = "claude-haiku-4-5-20251001"

# How long between council sessions (seconds). Default: 1 hour.
SESSION_INTERVAL = int(os.environ.get("COUNCIL_INTERVAL", 3600))

# Max time one session is allowed to run (seconds)
SESSION_TIMEOUT = 120

# Minimum confidence score for Verity to approve a law
APPROVAL_THRESHOLD = 0.55

# Seeds — topics Herald will investigate, cycling through indefinitely
SEEDS = [
    "mechanistic interpretability of large language models",
    "quantum error correction thresholds",
    "diffusion models for protein structure prediction",
    "reinforcement learning from human feedback alignment",
    "federated learning and privacy preservation",
    "graph neural networks for drug discovery",
    "neural scaling laws and emergent capabilities",
    "causal inference in observational health data",
    "sparse autoencoders and feature decomposition",
    "multi-agent reinforcement learning coordination",
    "foundation models for robotics and embodied AI",
    "retrieval-augmented generation and knowledge grounding",
    "transformer alternatives: state space models and Mamba",
    "in-context learning and few-shot generalization",
    "AI-assisted theorem proving and formal verification",
    "latent diffusion and controllable generation",
    "biological neural circuit mapping connectomics",
    "superconducting qubit coherence improvements",
    "whole-brain emulation and neuromorphic computing",
    "symbolic AI revival and neuro-symbolic integration",
]

# Paths
import pathlib
ROOT        = pathlib.Path(__file__).parent
CANON_FILE  = ROOT / "laws" / "canon.json"
SESSIONS_DIR = ROOT / "laws" / "sessions"
