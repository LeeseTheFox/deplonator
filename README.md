# 🤖 Deplonator

> A self-hosted web panel for deploying and managing your Telegram and Discord bot collection using Docker containers.

---

## Features

- Deploy bots as isolated Docker containers with a single click
- Upload and manage project files directly from the browser
- Container lifecycle management — start, stop, restart, redeploy
- Real-time log viewing and monitoring
- Maintenance mode for live debugging inside the container
- Per-project configuration (requirements file, startup script, auto-start)

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

---

## Getting Started

1. **Clone the repository:**
```bash
git clone https://github.com/LeeseTheFox/deplonator.git
cd deplonator
```

2. **Start the panel:**
```bash
docker compose up -d
```

The web interface will be available on **port `5643`** of your host machine.  
Open `http://<your-server-ip>:5643` in your browser.

---

## Data Persistence

Project files and the database are stored in the `./data` directory on your host, mounted into the container automatically. Your data is safe across container restarts and upgrades.

---

## Updating

To pull the latest changes and rebuild:

```bash
git pull
docker compose up -d --build
```

If a database schema change was introduced, you must apply the migrations to your database after the container has been built and restarted:

```bash
docker exec -it telegram-bot-deployer alembic upgrade head
```