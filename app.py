import os
import jwt # <-- NOVA IMPORTAÇÃO
import datetime # <-- NOVA IMPORTAÇÃO
from functools import wraps # <-- NOVA IMPORTAÇÃO
from flask import Flask, request, jsonify # type: ignore
from flask_cors import CORS # type: ignore
import psycopg2 # type: ignore
from dotenv import load_dotenv # type: ignore

from flask_cors import CORS # type: ignore
# ...
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


app.config['SECRET_KEY'] = os.getenv('JWT_SECRET', 'chave-seguranca-padrao')

DB_CONFIG = {
    'dbname': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'), 
    'host': os.getenv('DB_HOST'),
    'port': os.getenv('DB_PORT')
}

@app.route('/api/auth/financeiro', methods=['POST'])
def auth_financeiro():
    data = request.get_json()
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Agora buscamos também o ID do gerente para colocar no crachá
        cursor.execute("SELECT id, nome FROM gerentes WHERE pin_acesso = %s;", (data.get('pin'),))
        manager = cursor.fetchone()
        cursor.close()
        conn.close()

        if manager:
            # O PIN ESTÁ CORRETO! Vamos fabricar o crachá (Token JWT)
            # Ele guarda quem é o gerente e tem uma validade de 2 horas
            token = jwt.encode({
                'id': manager[0],
                'nome': manager[1],
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=2)
            }, app.config['SECRET_KEY'], algorithm="HS256")

            # Entregamos o crachá na resposta
            return jsonify({
                'status': 'sucesso', 
                'nome': manager[1],
                'token': token 
            }), 200
            
        return jsonify({'status': 'erro', 'mensagem': 'PIN incorreto'}), 401
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

@app.route('/api/matriculas', methods=['POST'])
def nova_matricula():
    try:
        # 1. Pega os dados enviados pelo Angular
        # 1. Pega os dados enviados pelo Angular
        dados = request.get_json()
        nome = dados.get('nome')
        telefone = dados.get('telefone')
        unidade = dados.get('unidade')
        plano = dados.get('plano')

        # 2. Conecta no PostgreSQL
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # 3. Insere o novo aluno na tabela
        query = """
            INSERT INTO alunos (nome, telefone, unidade, plano, status, ultimo_acesso)
            VALUES (%s, %s, %s, %s, 'Ativo', CURRENT_DATE)
        """
        cursor.execute(query, (nome, telefone, unidade, plano))
        
        # 4. Salva e fecha a conexão
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"mensagem": "Matrícula realizada com sucesso!"}), 201

    except Exception as e:
        print("Erro ao salvar matrícula:", e)
        return jsonify({"erro": "Falha interna no servidor"}), 500

@app.route('/api/alunos', methods=['GET'])
def listar_alunos():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT id, nome, telefone, unidade, plano, TO_CHAR(data_matricula, 'DD/MM/YYYY às HH24:MI') FROM alunos ORDER BY id DESC;")
        alunos_db = cursor.fetchall()
        cursor.close()
        conn.close()
        lista_alunos = [{'id': f"#{a[0]}", 'nome': a[1], 'telefone': a[2], 'unidade': a[3], 'plano': a[4], 'ultimoAcesso': a[5], 'status': 'Em dia'} for a in alunos_db]
        return jsonify(lista_alunos), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

@app.route('/api/dashboard/visao-geral', methods=['GET'])
def visao_geral():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM alunos;")
        alunos_ativos = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM alunos WHERE DATE(data_matricula) = CURRENT_DATE;")
        inscricoes_hoje = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        horas_mock = [
            {'time': '00h', 'visitors': 5}, {'time': '01h', 'visitors': 2},
            {'time': '02h', 'visitors': 0}, {'time': '03h', 'visitors': 0},
            {'time': '04h', 'visitors': 0}, {'time': '05h', 'visitors': 15},
            {'time': '06h', 'visitors': 45}, {'time': '07h', 'visitors': 85},
            {'time': '08h', 'visitors': 70}, {'time': '09h', 'visitors': 50},
            {'time': '10h', 'visitors': 40}, {'time': '11h', 'visitors': 35},
            {'time': '12h', 'visitors': 60}, {'time': '13h', 'visitors': 55},
            {'time': '14h', 'visitors': 40}, {'time': '15h', 'visitors': 45},
            {'time': '16h', 'visitors': 65}, {'time': '17h', 'visitors': 110},
            {'time': '18h', 'visitors': 145}, {'time': '19h', 'visitors': 130},
            {'time': '20h', 'visitors': 95}, {'time': '21h', 'visitors': 60},
            {'time': '22h', 'visitors': 30}, {'time': '23h', 'visitors': 15}
        ]

        total_catraca = sum([h['visitors'] for h in horas_mock])

        return jsonify({
            'metricas': {'inscricoesHoje': inscricoes_hoje, 'alunosAtivos': alunos_ativos, 'acessosCatraca': total_catraca, 'cancelamentos': 2}, 
            'origens': {'site': {'quantidade': inscricoes_hoje, 'porcentagem': 100 if inscricoes_hoje > 0 else 0}, 'presencial': {'quantidade': 0, 'porcentagem': 0}, 'gympass': {'quantidade': 0, 'porcentagem': 0}}, 
            'peakHours': horas_mock
        }), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

@app.route('/api/financeiro/despesas/<int:id>', methods=['PUT', 'DELETE'])
def gerenciar_despesa(id):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        if request.method == 'DELETE':
            cursor.execute("DELETE FROM despesas WHERE id = %s", (id,))
        elif request.method == 'PUT':
            dados = request.get_json()
            if 'status' in dados:
                cursor.execute("UPDATE despesas SET status = %s WHERE id = %s", (dados['status'], id))
            elif 'valor' in dados:
                cursor.execute("UPDATE despesas SET valor = %s WHERE id = %s", (dados['valor'], id))

        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'sucesso'}), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

@app.route('/api/financeiro/despesas', methods=['POST'])
def nova_despesa():
    dados = request.get_json()
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        query = "INSERT INTO despesas (categoria, vencimento, valor, status, unidade) VALUES (%s, %s, %s, %s, %s)"
        cursor.execute(query, (dados.get('categoria'), dados.get('vencimento'), dados.get('valor'), dados.get('status'), dados.get('unidade')))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'sucesso'}), 201
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    
@app.route('/api/profissionais', methods=['GET'])
def listar_profissionais():
    unidade = request.args.get('unidade')
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        query = "SELECT nome, cargo, TO_CHAR(horario_inicio, 'HH24:MI'), TO_CHAR(horario_fim, 'HH24:MI'), dias_semana FROM profissionais WHERE unidade = %s"
        cursor.execute(query, (unidade,))
        equipe = cursor.fetchall()
        
        lista = [{'nome': e[0], 'cargo': e[1], 'inicio': e[2], 'fim': e[3], 'dias': e[4]} for e in equipe]
        
        cursor.close()
        conn.close()
        return jsonify(lista), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

@app.route('/api/financeiro/dados', methods=['GET'])
def dados_financeiros():
    unidade = request.args.get('unidade')
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # 1. Busca as despesas cadastradas
        cursor.execute("SELECT id, categoria, TO_CHAR(vencimento, 'DD/MM/YYYY'), valor, status FROM despesas WHERE unidade = %s ORDER BY vencimento ASC;", (unidade,))
        despesas_db = cursor.fetchall()
        lista_despesas = [{'id': d[0], 'categoria': d[1], 'vencimento': d[2], 'valor': float(d[3]), 'status': d[4]} for d in despesas_db]
        total_despesas = sum([d['valor'] for d in lista_despesas if d['status'] != 'Cancelado'])

        # 2. Conta os alunos reais por plano
        cursor.execute("SELECT plano, COUNT(*) FROM alunos WHERE unidade = %s GROUP BY plano;", (unidade,))
        planos_db = cursor.fetchall()
        
        contagem = {'anual': 0, 'semestral': 0, 'mensal': 0}
        for p in planos_db:
            plano_nome = p[0].lower() if p[0] else 'mensal'
            if 'anual' in plano_nome: contagem['anual'] += p[1]
            elif 'semestral' in plano_nome: contagem['semestral'] += p[1]
            else: contagem['mensal'] += p[1]

        total_alunos = sum(contagem.values())
        distribuicao = {'anual': 0, 'semestral': 0, 'mensal': 0}
        if total_alunos > 0:
            distribuicao['anual'] = round((contagem['anual'] / total_alunos) * 100)
            distribuicao['semestral'] = round((contagem['semestral'] / total_alunos) * 100)
            distribuicao['mensal'] = 100 - (distribuicao['anual'] + distribuicao['semestral'])

        # 3. Matemática Financeira 100% REAL
        # Multiplica a quantidade exata de alunos pelo valor de cada plano
        faturamento = (contagem['anual'] * 89.90) + (contagem['semestral'] * 99.90) + (contagem['mensal'] * 119.90)
        
        # AQUI FOI REMOVIDO O VALOR FICTÍCIO DE R$ 142.500,00

        receita = faturamento - total_despesas
        inadimplencia = faturamento * 0.034 # Mantido a taxa de 3.4% sobre o faturamento real

        cursor.close()
        conn.close()
        return jsonify({
            'despesas': lista_despesas, 
            'planDistribution': distribuicao,
            'resumo': {'faturamento': faturamento, 'receita': receita, 'inadimplencia': inadimplencia}
        }), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)