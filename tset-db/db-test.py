from minidb import Database
db = Database("first-db")

db.users.insert_one({"username": "admin", "password": ""})
