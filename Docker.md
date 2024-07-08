# How to run the bot inside a docker container
## Motivation
Docker is a service that helps in creating isolation in the local environment. For example, if your machine runs on Windows with Python 2, you won't have to worry about running the bot that has been developed on Linux with Python 3.7  or 3.8.

The introduced `Dockerfile` and `docker-compose.yml` uses `Ubuntu 20.04` and `Python3.8` to run the bot in an isolated environment.

### Clone the Repository

```bash
$ git clone https://github.com/goats-9/TLE
```

### Set up Environment Variables

- Create a new file `environment` from `environment.template`.

```bash
cp environment.template environment
```

Fill in appropriate variables in new "environment" file.

- Open the file `environment`.
```bash
export BOT_TOKEN="XXXXXXXXXXXXXXXXXXXXXXXX.XXXXXX.XXXXXXXXXXXXXXXXXXXXXXXXXXX"
export LOGGING_COG_CHANNEL_ID="XXXXXXXXXXXXXXXXXX"
```
- Change the value of `BOT_TOKEN` with the token of the bot you created from [this step](https://github.com/reactiflux/discord-irc/wiki/Creating-a-discord-bot-&-getting-a-token).

- Replace the value of `LOGGING_COG_CHANNEL_ID` with discord [channel id](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID-) that you want to use as a logging channel.

### Build and Run the Container

Navigate to `TLE` and run the container using the following command.
```bash
$ sudo docker compose up --build # sudo may be omitted if docker can be run in rootless mode
```

