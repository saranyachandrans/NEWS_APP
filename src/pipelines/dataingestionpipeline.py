from src.components.dataingestion import DataIngestion


class DataIngestionPipeline:

    def __init__(self, config_path: str = "config.yaml"):
        self.data_ingestion = DataIngestion(config_path)

    def run_pipeline(self):
        news_data = self.data_ingestion.initiate_data_ingestion()
        print(f"Data ingestion completed: {len(news_data)} articles in file")
        return news_data


if __name__ == "__main__":
    DataIngestionPipeline().run_pipeline()
