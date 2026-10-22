"""Run the pipelines.

    python main.py                  # ingestion + cleaning
    python main.py --stage ingest
    python main.py --stage clean
"""
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["ingest", "clean", "all"], default="all")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    # imported lazily so the clean stage works without feedparser installed
    if args.stage in ("ingest", "all"):
        from src.pipelines.dataingestionpipeline import DataIngestionPipeline
        DataIngestionPipeline(args.config).run_pipeline()
    if args.stage in ("clean", "all"):
        from src.pipelines.datacleaningpipeline import DataCleaningPipeline
        DataCleaningPipeline(args.config).run_pipeline()
