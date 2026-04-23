import uvicorn

if __name__ == "__main__":
    print("\n  Zotero Semantic Search → http://127.0.0.1:8000\n")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="info")
