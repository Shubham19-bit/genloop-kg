# Windows PowerShell: full pipeline end to end (needs internet for download, Ollama for LLM steps)
python -m src.cli download
python -m src.cli parse
python -m src.cli extract
python -m src.cli extract-llm
python -m src.cli build
python -m src.cli eval
