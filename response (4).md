## Вариант №1 (рекомендую): Open WebUI + Ollama
Open WebUI — готовый веб‑интерфейс для Ollama: чат, несколько пользователей, сохранение диалогов, загрузка файлов (в т.ч. PDF) и “вопросы по документам”.

### 1) Проверьте, что Ollama отвечает локально
На сервере:
```bash
curl http://127.0.0.1:11434/api/tags
```
Должен вернуться JSON со списком моделей (включая `qwen2.5:7b`).

> Если у вас Ollama слушает только localhost — это нормально и даже лучше для безопасности.

---

### 2) Поставьте Docker (если ещё нет)
Ubuntu:
```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

---

### 3) Поднимите Open WebUI через docker compose
Создайте каталог и файл:
```bash
sudo mkdir -p /opt/openwebui
sudo nano /opt/openwebui/docker-compose.yml
```

Содержимое `docker-compose.yml`:
```yaml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: unless-stopped
    ports:
      - "3000:8080"
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
      # желательно задать свой ключ (любая длинная случайная строка)
      - WEBUI_SECRET_KEY=CHANGE_ME_TO_A_LONG_RANDOM_STRING
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - openwebui-data:/app/backend/data

volumes:
  openwebui-data:
```

Запуск:
```bash
cd /opt/openwebui
sudo docker compose up -d
sudo docker ps
```

Открывайте в браузере с локального ПК:
- `http://IP_СЕРВЕРА:3000`

**Важно про пользователей:** первый зарегистрированный пользователь обычно становится **admin** (дальше сможете управлять регистрацией/доступом).

---

### 4) PDF “для обработки” (вопросы по документам / извлечение смысла)
Чтобы качественно работать с PDF через RAG, обычно нужен embedding‑модуль. Часто используют:
```bash
ollama pull nomic-embed-text
```
Дальше в Open WebUI включаете/настраиваете “Documents / Knowledge / RAG” (названия пунктов могут чуть отличаться по версии), загружаете PDF, и затем задаёте вопросы — UI сам будет подтягивать релевантные фрагменты документа в контекст модели.

---

### 5) Сохранение диалогов
Open WebUI **сам сохраняет историю** по пользователям. Данные лежат в docker‑томе `openwebui-data`.

Бэкап (простой вариант):
```bash
sudo docker volume inspect openwebui-data
```
Посмотрите `Mountpoint` и делайте резервную копию каталога, либо делайте бэкап средствами Docker.

---

# Безопасность (очень советую)
### Не открывайте Ollama наружу без нужды
Оставьте Ollama на `127.0.0.1:11434`, а наружу отдавайте только WebUI.

### Ограничьте доступ к WebUI только из локальной сети (пример ufw)
Допустим, ваша LAN: `192.168.1.0/24`:
```bash
sudo ufw allow from 192.168.1.0/24 to any port 3000 proto tcp
sudo ufw deny 3000/tcp
sudo ufw enable
sudo ufw status
```

# Автозапуск: systemd сервис
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
```bash

```

