# Request bounds: everything past these reaches the embedder and the database for nothing.
QUERY_MAX_CHARS = 2000
SEARCH_TOP_N_MAX = 50
# Reranked chunks given to the model per answer: about 3k tokens at CHUNK_MAX_CHARS.
ANSWER_SOURCES = 8
# Seconds per OpenAI request; the SDK retries timeouts and 429/5xx this many times.
GENERATE_TIMEOUT_S = 60.0
GENERATE_MAX_RETRIES = 2
