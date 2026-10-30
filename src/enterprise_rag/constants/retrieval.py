# bge-m3 output size. Changing it needs a migration and a full re-embed.
EMBEDDING_DIM = 1024

FTS_LANGUAGE = "english"

# Defaults; each is overridable through .env (see Settings).
DEFAULT_RRF_K = 60
DEFAULT_SEARCH_CANDIDATES = 50
DEFAULT_SEARCH_TOP_N = 8
# Must stay >= search candidates, or HNSW returns fewer rows than asked.
DEFAULT_HNSW_EF_SEARCH = 100
# Hybrid-search hits handed to the cross-encoder per query; the API returns the best top_n of them.
# Must stay >= SEARCH_TOP_N_MAX. Cost grows linearly: each candidate is a full model pass.
RERANK_CANDIDATES = 50
# Texts per TEI /rerank request. TEI rejects batches above its --max-client-batch-size (32).
RERANK_BATCH_SIZE = 32
# Seconds per TEI /rerank request; 50 chunks on CPU take a few seconds.
RERANK_TIMEOUT_S = 30.0
