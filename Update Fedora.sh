#!/bin/sh
sudo dnf makecache -y        # обновление локального кэша репозиториев (аналог apt-get update)
sudo dnf upgrade -y          # обновление всех установленных пакетов (аналог apt-get upgrade)
sudo dnf distro-sync -y      # синхронизация системы с репозиториями, может удалять/добавлять пакеты (аналог apt-get dist-upgrade)