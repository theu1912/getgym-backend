from flask import Flask, request, jsonify # type: ignore
from flask_cors import CORS, cross_origin # type: ignore
import psycopg2 # type: ignore

app = Flask(__name__)
CORS(app)

DB_CONFIG = {
    'dbname': 'getgym_db', # A alteração vital está aqui!
    'user': 'postgres',
    'password': 'theu1', 
    'host': 'localhost',
    'port': '5432'
}

# 1. Rota de Autenticação do Financeiro
@app.route('/api/auth/financeiro', methods=['POST', 'OPTIONS'])
@cross_origin()
def auth_financeiro():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    data = request.get_json()
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT nome FROM gerentes WHERE pin_acesso = %s;", (data.get('pin'),))
        manager = cursor.fetchone()
        cursor.close()
        conn.close()
        if manager: return jsonify({'status': 'sucesso', 'nome': manager[0]}), 200
        return jsonify({'status': 'erro', 'mensagem': 'PIN incorreto'}), 401
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

# 2. Nova Rota de Matrículas
@app.route('/api/matriculas', methods=['POST', 'OPTIONS'])
@cross_origin()
def nova_matricula():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    dados = request.get_json()
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        query = "INSERT INTO alunos (nome, telefone, unidade, plano, data_matricula) VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)"
        cursor.execute(query, (dados.get('nome'), dados.get('telefone'), dados.get('unidade'), dados.get('plano')))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'sucesso', 'mensagem': 'Aluno cadastrado!'}), 201
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

# 3. Rota de Listagem de Alunos
@app.route('/api/alunos', methods=['GET'])
@cross_origin()
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

# 4. Rota da Visão Geral (Dashboard)
@app.route('/api/dashboard/visao-geral', methods=['GET'])
@cross_origin()
def visao_geral():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM alunos WHERE DATE(data_matricula) = CURRENT_DATE;")
        inscricoes_hoje = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM alunos;")
        alunos_ativos = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return jsonify({
            'metricas': {'inscricoesHoje': inscricoes_hoje, 'alunosAtivos': alunos_ativos, 'acessosCatraca': 0, 'cancelamentos': 0},
            'origens': {'site': {'quantidade': inscricoes_hoje, 'porcentagem': 100 if inscricoes_hoje > 0 else 0}, 'presencial': {'quantidade': 0, 'porcentagem': 0}, 'gympass': {'quantidade': 0, 'porcentagem': 0}},
            'peakHours': [{'time': '06h', 'visitors': 45}, {'time': '08h', 'visitors': 85}, {'time': '18h', 'visitors': 140}, {'time': '20h', 'visitors': 110}]
        }), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500



@app.route('/api/financeiro/despesas/<int:id>', methods=['PUT', 'DELETE', 'OPTIONS'])
@cross_origin()
def gerenciar_despesa(id):
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
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
    
    # ROTA DE PROFISSIONAIS (FILTRADA POR UNIDADE)
@app.route('/api/profissionais', methods=['GET'])
@cross_origin()
def listar_profissionais():
    unidade = request.args.get('unidade') # O Angular vai passar ?unidade=Fazendinha
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

# ATUALIZAÇÃO DO FINANCEIRO PARA FILTRAR POR UNIDADE
@app.route('/api/financeiro/dados', methods=['GET'])
@cross_origin()
def dados_financeiros():
    unidade = request.args.get('unidade')
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Busca apenas despesas daquela unidade específica
        cursor.execute("SELECT id, categoria, TO_CHAR(vencimento, 'DD/MM/YYYY'), valor, status FROM despesas WHERE unidade = %s ORDER BY vencimento ASC;", (unidade,))
        despesas_db = cursor.fetchall()
        lista_despesas = [{'id': d[0], 'categoria': d[1], 'vencimento': d[2], 'valor': d[3], 'status': d[4]} for d in despesas_db]

        # Distribuição de planos também filtrada por unidade
        cursor.execute("SELECT plano, COUNT(*) FROM alunos WHERE unidade = %s GROUP BY plano;", (unidade,))
        planos_db = cursor.fetchall()
        total_alunos = sum([p[1] for p in planos_db])
        distribuicao = {'anual': 0, 'semestral': 0, 'mensal': 0}
        
        if total_alunos > 0:
            for p in planos_db:
                if 'Anual' in p[0]: distribuicao['anual'] = int((p[1] / total_alunos) * 100)
                elif 'Semestral' in p[0]: distribuicao['semestral'] = int((p[1] / total_alunos) * 100)
                elif 'Mensal' in p[0]: distribuicao['mensal'] = int((p[1] / total_alunos) * 100)
        
        cursor.close()
        conn.close()
        return jsonify({'despesas': lista_despesas, 'planDistribution': distribuicao}), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)