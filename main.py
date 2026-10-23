"""Run the pipelines.

    python main.py                       # ingest -> clean -> preprocess
    python main.py --stage ingest
    python main.py --stage clean
    python main.py --stage preprocess    # text -> vectors
"""
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["ingest", "clean", "preprocess", "all"], default="all")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    # imported lazily so each stage only needs its own libraries installed
    if args.stage in ("ingest", "all"):
        from src.pipelines.dataingestionpipeline import DataIngestionPipeline
        DataIngestionPipeline(args.config).run_pipeline()
    if args.stage in ("clean", "all"):
        from src.pipelines.datacleaningpipeline import DataCleaningPipeline
        DataCleaningPipeline(args.config).run_pipeline()
    if args.stage in ("preprocess", "all"):
        from src.pipelines.datapreprocessingpipeline import DataPreprocessingPipeline
        DataPreprocessingPipeline(args.config).run_pipeline()
