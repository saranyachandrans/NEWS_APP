from src.components.datacleaning import DataCleaning


class DataCleaningPipeline:

    def __init__(self, config_path: str = "config.yaml"):
        self.data_cleaning = DataCleaning(config_path)

    def run_pipeline(self):
        cleaned = self.data_cleaning.initiate_data_cleaning()
        print(f"Data cleaning completed: {len(cleaned)} clean articles")
        return cleaned


if __name__ == "__main__":
    DataCleaningPipeline().run_pipeline()
