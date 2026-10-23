from src.components.datapreprocessing import DataPreprocessing


class DataPreprocessingPipeline:

    def __init__(self, config_path: str = "config.yaml"):
        self.data_preprocessing = DataPreprocessing(config_path)

    def run_pipeline(self):
        vectorized = self.data_preprocessing.initiate_data_preprocessing()
        field = self.data_preprocessing.s.get("vector_field", "vector")
        print(f"Data preprocessing completed: {len(vectorized)} articles, "
              f"{len(vectorized[0][field])}-dimensional vectors")
        return vectorized


if __name__ == "__main__":
    DataPreprocessingPipeline().run_pipeline()
