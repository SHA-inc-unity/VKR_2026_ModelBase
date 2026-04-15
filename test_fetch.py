from catboost_floader.data_ingestion import fetch_and_save_bybit_data

df = fetch_and_save_bybit_data(lookback_days=2)
print(df.head())
print(df.tail())
print(df.shape)