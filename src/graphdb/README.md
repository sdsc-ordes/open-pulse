# Graph DB


## How to configure your `.env` file?

Please create a graphDB folder where the data and config will be stored.

```

```

## How to configure your `user.properties`?

Please configure your password. You can encript it using bcrypt

```
graphdb.auth.admin.username=admin
graphdb.auth.admin.password={bcrypt}sodjfoijwef
graphdb.workbench.auth.enabled=true
```


## How to deploy this service?

```
docker compose config
docker compose up -d
```


```
docker compose down
```