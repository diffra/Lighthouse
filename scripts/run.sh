#!/bin/bash
cd ..
docker-compose down
docker-compose build
docker-compose up -d
docker-compose logs -f python
docker-compose down
cd scripts
