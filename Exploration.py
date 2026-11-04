import pandas as pd

categories_df = pd.read_csv('data/categories.csv')
customers_df = pd.read_csv('data/customers.csv')
order_items_df = pd.read_csv('data/order_items.csv')
orders_df = pd.read_csv('data/orders.csv')
products_df = pd.read_csv('data/products.csv')
promotions_df = pd.read_csv('data/promotions.csv')

print(categories_df.to_string())
print(customers_df.to_string())
print(order_items_df.to_string())
print(orders_df.to_string())
print(products_df.to_string())
print(promotions_df.to_string())