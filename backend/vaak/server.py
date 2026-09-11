from __future__ import annotations

from .product.bootstrap import build_app

app = build_app()


def main():
    import uvicorn
    uvicorn.run("vaak.server:app", host="0.0.0.0", port=8080, reload=False)


if __name__ == "__main__":
    main()
