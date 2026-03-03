# Portainer

## How to configure your `.env` file.

Configure the admin password with an bcrypt version of your password. Please pay attention and double `$` when adding the encrypted password. Otherwise, linux will not parse it correctly.

In linux:
```
sudo apt-get install apache2-utils
htpasswd -nbBC 10 admin yourpassword
```

## How to run this docker compose?

```
docker compose up -d
```

And then you can turn it down with

```
docker compose down
```
