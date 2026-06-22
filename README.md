# GetGym — Backend

API REST desenvolvida em Flask + PostgreSQL para o sistema de gestão da GetGym, rede de academias com 3 unidades em Curitiba (Fazendinha, Piraquara, Pinhais).

## Stack

- Python + Flask
- PostgreSQL
- PyJWT (autenticação HS256)
- Make.com (webhook de matrículas)

## Funcionalidades

- Autenticação JWT para admin geral e financeiro por unidade
- Matrículas com disparo de webhook para Make.com
- CRUD de despesas por unidade com PIN
- Dashboard KPIs (alunos ativos, faturamento, matrículas por mês)
- Grade de profissionais e aulas

## Como rodar

```bash
pip install -r requirements.txt
```

Crie um `.env` na raiz baseado no `.env.example` do repositório.

```env
DATABASE_URL=postgresql://usuario:senha@localhost/getgym
JWT_SECRET_KEY=sua_chave_secreta
ADMIN_USUARIO=admin
ADMIN_SENHA=getgym2026
WEBHOOK_MATRICULA=url_do_make
```

```bash
python app.py
```

API disponível em `http://localhost:5000`

## Repositório Frontend

https://github.com/theu1912/getgym
