# Portainer

## How to configure your `.env` file. 

Configure the admin password with an bcrypt version of your password. 

In linux:

```
sudo apt-get install apache2-utils  
htpasswd -nbBC 10 admin yourpassword
```

## How to run this docker compose?

```
docker compose up -d
```