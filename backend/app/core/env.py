"""Environment setup that must run before any Hugging Face import."""

import os

import certifi


def configure_env() -> None:
    """Set model-download and TLS settings before importing HF libraries."""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    ca_bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)

    # Avoid the tokenizer fork warning and nondeterministic thread pools.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
