# bge-m3 output size. Changing it needs a migration and a full re-embed.
EMBEDDING_DIM = 1024

FTS_LANGUAGE = "english"

# Defaults; each is overridable through .env (see Settings).
DEFAULT_RRF_K = 60
DEFAULT_SEARCH_CANDIDATES = 50
DEFAULT_SEARCH_TOP_N = 8
# Must stay >= search candidates, or HNSW returns fewer rows than asked.
DEFAULT_HNSW_EF_SEARCH = 100
