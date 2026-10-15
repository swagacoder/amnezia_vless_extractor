# amnezia_vless_extractor
Extract VLESS config from Amnezia VPN client

1) Выбираем интересующий нас сервер (например Нидерланды) и обязательно выбираем протокол VLESS
<img width="382" height="712" alt="image" src="https://github.com/user-attachments/assets/6b85735d-3adf-4970-a233-3ccb652c3227" />

2) Далее нажимаем Настройки -> Резервное копирование -> Создать резервную копию
<img width="382" height="712" alt="image" src="https://github.com/user-attachments/assets/c7f19c5a-51c7-4785-a3b8-61c0f310c7c8" />

3) Сохраняем резервную копию в любое удобное место (повторяем так для каждого интересующего сервера)
4) Запускаем extractor.py в терминале с флагом --runtime-only (необходимо сделать так для каждого бэкапа)
```shell
python extractor.py path_to_your_backup --runtime-only
```
После чего в терминале должны увидеть следующее
```shell

========== RELAY ==========
vless://***

XHTTP:
{
  "path": "/",
  "mode": "auto"
}

EXTRA:
{
  "xmux": {
    "maxConnections": 4
  }
}

========== EXIT ==========
vless://***

После импорта в Shadowrocket:
EXIT -> Proxy Pass -> Amnezia Нидерланды relay
```
Здесь указаны две vless:// ссылки конфигурации RELAY и EXIT, копируем RELAY

5) Запускаем приложение Shadowrocket и нажимаем "+", чтобы добавить конфигурацию, вставляем туда vless:// RELAY ссылку
6) DONE! P.S. некоторые сервера могут просто не работать :(
