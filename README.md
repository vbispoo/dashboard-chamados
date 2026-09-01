# Dashboard de Atendimento — Equipe 57

Dashboard web para acompanhamento de chamados atendidos pela equipe, com dados em tempo real buscados do portal corporativo Apdata.

## Funcionalidades

- Ranking da equipe com total de chamados no ano
- Visão individual por colaborador com gráficos mensais
- KPIs dinâmicos (total, média, acumulado, variação)
- Atualização automática a cada 2 minutos
- Tema claro / escuro
- Seleção interativa de mês com detalhe e gráficos destacados

## Como rodar

### 1. Instalar dependências

```bash
pip install -r requirements.txt
```

### 2. Configurar credenciais

Crie um arquivo `.env` na raiz do projeto (nunca commitar):

```
APP_USER=seu_usuario
APP_PASS=sua_senha
```

### 3. Iniciar o servidor

```bash
python app.py
```

### 4. Acessar o dashboard

Abra no navegador: [http://localhost:5000](http://localhost:5000)

## Estrutura

```
├── app.py                   # Servidor Flask + cache + scheduler
├── Requisicao.py            # Módulo de comunicação com o portal Apdata
├── dashboard-chamados.html  # Frontend do dashboard
├── requirements.txt         # Dependências Python
├── .env                     # Credenciais (não versionado)
└── .gitignore
```

## Tecnologias

- **Backend:** Python 3.11+, Flask, flask-cors
- **Frontend:** HTML/CSS/JS puro, Chart.js 4
- **Dados:** Portal corporativo Apdata via API HTTP
