<think>Пользователь хочет, чтобы Open WebUI запускался автоматически при перезагрузке сервера. Есть несколько способов:

1. **docker compose restart policy** - самый простой для Docker контейнеров
2. **systemd сервис** - для управления docker compose
3. **docker run --restart** - если запускать напрямую

Самый надежный способ - это либо:
1. Включить `restart: always` в docker-compose.yml (но это не совсем правильно, потому что docker daemon должен быть запущен)
2. Создать systemd сервис для docker compose

Рекомендую оба варианта:
- Быстрый: добавить `restart: always` в docker-compose.yml
- Правильный: создать systemd сервис

Или самый простой для новичка: просто добавить в docker-compose.yml строку `restart: always` или `restart: unless-stopped`</think>Есть несколько способов. **Рекомендую самый простой — через docker-compose.yml:**

### Способ 1 (самый простой): `restart: always` в docker-compose.yml

Отредактируйте файл:
```bash
sudo nano /opt/openwebui/docker-compose.yml
```

Добавьте строку `restart: always`:
```yaml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: always              # ← ЭТА СТРОКА
    ports:
      - "3000:8080"
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
      - WEBUI_SECRET_KEY=CHANGE_ME_TO_A_LONG_RANDOM_STRING
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - openwebui-data:/app/backend/data

volumes:
  openwebui-data:
```

Сохраните (Ctrl+X → Y → Enter) и перезапустите:
```bash
cd /opt/openwebui
sudo docker compose up -d
```

Теперь контейнер будет автоматически запускаться при перезагрузке сервера (если Docker daemon запущен).

---

### Способ 2 (правильнее): systemd сервис
Если хотите управлять через `systemctl`:

Создайте файл:
```bash
sudo nano /etc/systemd/system/open-webui.service
```

Содержимое:
```ini
[Unit]
Description=Open WebUI Service
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/openwebui
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Включите и запустите:
```bash
sudo systemctl daemon-reload
sudo systemctl enable open-webui
sudo systemctl start open-webui
sudo systemctl status open-webui
```

Проверьте при перезагрузке:
```bash
sudo reboot
# после перезагрузки:
sudo docker ps
```

---

### Проверка, что всё работает:
```bash
# Посмотрите логи:
sudo docker compose -f /opt/openwebui/docker-compose.yml logs -f

# или через systemctl (если способ 2):
sudo journalctl -u open-webui -f
```

**Я рекомендую Способ 1** — просто добавить `restart: always` в YAML. Это стандартно и надёжно.

Какой выберете?