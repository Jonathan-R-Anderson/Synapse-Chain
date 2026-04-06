FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY ensure_i2p_router_sam.py /app/ensure_i2p_router_sam.py
COPY execution/src/crates /app/execution/src/crates

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install \
      /app/execution/src/crates/primitives \
      /app/execution/src/crates/crypto \
      /app/execution/src/crates/encoding \
      /app/execution/src/crates/state \
      /app/execution/src/crates/zk \
      /app/execution/src/crates/transactions \
      /app/execution/src/crates/evm \
      /app/execution/src/crates/execution

WORKDIR /var/lib/crypto/execution

EXPOSE 8545

CMD ["sh", "-c", "python -m rpc.server --host \"${EXECUTION_RPC_HOST:-0.0.0.0}\" --port \"${EXECUTION_RPC_PORT:-8545}\" --chain-id \"${EXECUTION_CHAIN_ID:-1337}\" --mode \"${EXECUTION_MINING_MODE:-instant}\""]
