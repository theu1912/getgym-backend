from flask import Flask, request, jsonify # type: ignore
from flask_cors import CORS # type: ignore
import psycopg2 # type: ignore
from psycopg2 import pool # type: ignore
import os
import jwt # type: ignore
import requests # type: ignore
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv # type: ignore
from functools import wraps

load_dotenv()

app = Flask(__name__)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Em produção, troque pela URL real do seu frontend
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', 'http://localhost:4200').split(',')
CORS(app, origins=ALLOWED_ORIGINS)

# ── Banco de dados (pool de conexões) ─────────────────────────────────────────
connection_pool = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    dbname=os.environ.get('DB_NAME', 'getgym_db'),
    user=os.environ.get('DB_USER', 'postgres'),
    password=os.environ.get('DB_PASSWORD'),   # nunca coloque a senha aqui!
    host=os.environ.get('DB_HOST', 'localhost'),
    port=os.environ.get('DB_PORT', '5432')
)

def get_conn():
    return connection_pool.getconn()

def put_conn(conn):
    connection_pool.putconn(conn)

# ── JWT ─────────────────────────────────────────────────────────────────────
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'troque-por-um-segredo-seguro')
JWT_ALGORITHM  = 'HS256'
JWT_EXP_HORAS  = 8

# ── Admin geral (hardcoded por enquanto) ──────────────────────────────────────
ADMIN_USUARIO = os.environ.get('ADMIN_USUARIO', 'admin')
ADMIN_SENHA   = os.environ.get('ADMIN_SENHA', 'getgym2026')

def gerar_token(payload: dict) -> str:
    """Gera um JWT a partir de um payload (deve incluir 'role'). Expira em JWT_EXP_HORAS."""
    dados = {
        **payload,
        'exp': datetime.now(timezone.utc) + timedelta(hours=JWT_EXP_HORAS),
        'iat': datetime.now(timezone.utc)
    }
    return jwt.encode(dados, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def requer_admin(f):
    """Decorator que exige um JWT válido (role 'admin' ou 'financeiro') no header Authorization: Bearer <token>"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'OPTIONS':
            return f(*args, **kwargs)

        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'status': 'erro', 'mensagem': 'Não autorizado'}), 401

        token = auth.split('Bearer ', 1)[1]
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return jsonify({'status': 'erro', 'mensagem': 'Sessão expirada'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'status': 'erro', 'mensagem': 'Não autorizado'}), 401

        if payload.get('role') not in ('admin', 'financeiro'):
            return jsonify({'status': 'erro', 'mensagem': 'Não autorizado'}), 401

        return f(*args, **kwargs)
    return decorated

# ── Validação de campos obrigatórios ──────────────────────────────────────────
def campos_presentes(data: dict, campos: list):
    """Retorna lista dos campos ausentes ou vazios."""
    return [c for c in campos if not data.get(c)]

def strip_strings(d: dict) -> dict:
    """Remove espaços e quebras de linha invisíveis de todos os valores string do dict.

    Resolve o bug do Make.com onde filtros 'Equal to' rejeitavam strings com
    espaço/newline no final vindas do frontend Angular.
    """
    return {k: v.strip() if isinstance(v, str) else v for k, v in d.items()}

# ─────────────────────────────────────────────────────────────────────────────
# SETUP INICIAL — garante coluna unidade e PINs por unidade na tabela gerentes
# ─────────────────────────────────────────────────────────────────────────────
def init_gerentes_unidades():
    """Adiciona coluna unidade e semeia um gerente por unidade se necessário."""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE gerentes ADD COLUMN IF NOT EXISTS unidade VARCHAR(50);")
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'uq_gerentes_unidade'
                ) THEN
                    ALTER TABLE gerentes
                    ADD CONSTRAINT uq_gerentes_unidade UNIQUE (unidade);
                END IF;
            END $$;
        """)
        for nome, pin, unidade in [
            ('Gerente Fazendinha', '1234', 'Fazendinha'),
            ('Gerente Piraquara',  '5678', 'Piraquara'),
            ('Gerente Pinhais',    '9012', 'Pinhais'),
        ]:
            cursor.execute(
                "INSERT INTO gerentes (nome, pin_acesso, unidade) VALUES (%s, %s, %s) "
                "ON CONFLICT (unidade) DO NOTHING;",
                (nome, pin, unidade)
            )
        conn.commit()
        cursor.close()
    except Exception as e:
        conn.rollback()
        print(f'[AVISO] init_gerentes_unidades: {e}')
    finally:
        put_conn(conn)

try:
    init_gerentes_unidades()
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# AUTENTICAÇÃO ADMIN GERAL
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/admin/login', methods=['POST', 'OPTIONS'])
def admin_login():
    """Login do admin geral. Body: {usuario, senha}. Credenciais hardcoded por enquanto."""
    if request.method == 'OPTIONS':
        return jsonify({'success': True}), 200

    dados = strip_strings(request.get_json() or {})
    usuario = dados.get('usuario', '')
    senha   = dados.get('senha', '')

    if usuario == ADMIN_USUARIO and senha == ADMIN_SENHA:
        token = gerar_token({'role': 'admin'})
        return jsonify({'success': True, 'token': token}), 200

    return jsonify({'error': 'Credenciais inválidas'}), 401

# ─────────────────────────────────────────────────────────────────────────────
# AUTENTICAÇÃO FINANCEIRA
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/financeiro/verificar-pin', methods=['POST', 'OPTIONS'])
def verificar_pin_unidade():
    """Verifica PIN individual por unidade. Body: {pin, unidade}"""
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    data = strip_strings(request.get_json() or {})
    pin     = data.get('pin', '')
    unidade = data.get('unidade', '')

    if not pin or not unidade:
        return jsonify({'status': 'erro', 'mensagem': 'PIN e unidade são obrigatórios'}), 400

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT nome FROM gerentes WHERE pin_acesso = %s AND unidade = %s;",
            (pin, unidade)
        )
        gerente = cursor.fetchone()
        cursor.close()
        if gerente:
            jwt_token = gerar_token({'unidade': unidade, 'role': 'financeiro'})
            return jsonify({'success': True, 'token': jwt_token, 'unidade': unidade}), 200
        return jsonify({'status': 'erro', 'mensagem': 'PIN incorreto para esta unidade'}), 401
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

@app.route('/api/auth/financeiro', methods=['POST', 'OPTIONS'])
def auth_financeiro():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    data = strip_strings(request.get_json() or {})
    pin = data.get('pin', '')
    if not pin:
        return jsonify({'status': 'erro', 'mensagem': 'PIN não informado'}), 400

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT nome FROM gerentes WHERE pin_acesso = %s;", (pin,))
        manager = cursor.fetchone()
        cursor.close()
        if manager:
            return jsonify({'status': 'sucesso', 'nome': manager[0]}), 200
        return jsonify({'status': 'erro', 'mensagem': 'PIN incorreto'}), 401
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

# ─────────────────────────────────────────────────────────────────────────────
# MATRÍCULAS (público — qualquer pessoa pode se cadastrar)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/matriculas', methods=['POST', 'OPTIONS'])
def nova_matricula():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    dados = strip_strings(request.get_json() or {})
    faltando = campos_presentes(dados, ['nome', 'email', 'telefone', 'unidade', 'plano'])
    if faltando:
        return jsonify({'status': 'erro', 'mensagem': f'Campos obrigatórios: {", ".join(faltando)}'}), 400

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO alunos (nome, telefone, unidade, plano, status, data_matricula)
               VALUES (%s, %s, %s, %s, 'Em dia', NOW()) RETURNING id""",
            (dados['nome'], dados['telefone'], dados['unidade'], dados['plano'])
        )
        novo_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

    # Webhook Make.com — dispara após commit; falha silenciosa (não bloqueia a matrícula)
    webhook_url = os.environ.get('WEBHOOK_MATRICULA')
    if webhook_url:
        try:
            payload_webhook = {
                'nome':     dados['nome'],
                'email':    dados['email'],
                'telefone': dados['telefone'],
                'unidade':  dados['unidade'],
                'plano':    dados['plano'],
                'data':     datetime.now().isoformat(),
                'tipo':     'nova_matricula'
            }
            requests.post(webhook_url, json=payload_webhook, timeout=5)
        except Exception:
            pass  # Webhook falhou — matrícula já foi salva, não reverter

    return jsonify({'success': True, 'id': novo_id}), 201

# ─────────────────────────────────────────────────────────────────────────────
# ALUNOS (público — leitura sem autenticação para o painel admin carregar)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/alunos', methods=['GET'])
def listar_alunos():
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, nome, telefone, unidade, plano, status,
                      TO_CHAR(data_matricula, 'DD/MM/YYYY') AS matricula,
                      data_matricula
               FROM alunos ORDER BY id DESC;"""
        )
        alunos_db = cursor.fetchall()
        cursor.close()
        lista = [
            {
                'id': a[0],
                'nome': a[1],
                'telefone': a[2] or '',
                'unidade': a[3] or 'Fazendinha',
                'plano': a[4] or 'Mensal Flex',
                'status': a[5] or 'Em dia',
                'matricula': a[6] or '',
                'data_matricula': a[7].isoformat() if a[7] else None
            }
            for a in alunos_db
        ]
        return jsonify(lista), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

@app.route('/api/alunos/<int:aluno_id>/status', methods=['PUT', 'OPTIONS'])
def atualizar_status_aluno(aluno_id):
    if request.method == 'OPTIONS':
        return '', 204
    dados = strip_strings(request.get_json() or {})
    novo_status = dados.get('status', '').strip()
    if novo_status not in ('Em dia', 'Atrasado', 'Inativo'):
        return jsonify({'error': 'Status inválido'}), 400
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE alunos SET status = %s WHERE id = %s',
            (novo_status, aluno_id)
        )
        conn.commit()
        cursor.close()
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        put_conn(conn)

# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD (público — carrega antes do login)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/dashboard/visao-geral', methods=['GET'])
def visao_geral():
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM alunos;")
        total_alunos = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM alunos WHERE DATE(data_matricula) = CURRENT_DATE;")
        inscricoes_hoje = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM alunos WHERE status = 'Em dia';")
        alunos_ativos = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM alunos WHERE status = 'Inativo';")
        cancelamentos = cursor.fetchone()[0]
        cursor.close()

        # TODO: substituir por dados reais da catraca quando disponível
        horas_mock = [
            {'time': '00h', 'visitors': 5},  {'time': '01h', 'visitors': 2},
            {'time': '02h', 'visitors': 0},  {'time': '03h', 'visitors': 0},
            {'time': '04h', 'visitors': 0},  {'time': '05h', 'visitors': 15},
            {'time': '06h', 'visitors': 45}, {'time': '07h', 'visitors': 85},
            {'time': '08h', 'visitors': 70}, {'time': '09h', 'visitors': 50},
            {'time': '10h', 'visitors': 40}, {'time': '11h', 'visitors': 35},
            {'time': '12h', 'visitors': 60}, {'time': '13h', 'visitors': 55},
            {'time': '14h', 'visitors': 40}, {'time': '15h', 'visitors': 45},
            {'time': '16h', 'visitors': 65}, {'time': '17h', 'visitors': 110},
            {'time': '18h', 'visitors': 145},{'time': '19h', 'visitors': 130},
            {'time': '20h', 'visitors': 95}, {'time': '21h', 'visitors': 60},
            {'time': '22h', 'visitors': 30}, {'time': '23h', 'visitors': 15},
        ]
        total_catraca = sum(h['visitors'] for h in horas_mock)

        return jsonify({
            'metricas': {
                'inscricoesHoje': inscricoes_hoje,
                'alunosAtivos': alunos_ativos,
                'acessosCatraca': total_catraca,
                'cancelamentos': cancelamentos
            },
            'origens': {
                'site':       {'quantidade': inscricoes_hoje, 'porcentagem': 100 if inscricoes_hoje > 0 else 0},
                'presencial': {'quantidade': 0, 'porcentagem': 0},
                'gympass':    {'quantidade': 0, 'porcentagem': 0},
            },
            'peakHours': horas_mock
        }), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

@app.route('/api/dashboard/matriculas-por-mes', methods=['GET'])
def matriculas_por_mes():
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', data_matricula), 'Mon/YY') AS mes,
                COUNT(*) AS total
            FROM alunos
            WHERE data_matricula >= NOW() - INTERVAL '6 months'
            GROUP BY DATE_TRUNC('month', data_matricula)
            ORDER BY DATE_TRUNC('month', data_matricula);
        """)
        rows = cursor.fetchall()
        cursor.close()
        return jsonify([{'mes': r[0], 'total': r[1]} for r in rows]), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

# ─────────────────────────────────────────────────────────────────────────────
# DESPESAS — rota simples (sem proteção de admin)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/despesas', methods=['POST', 'OPTIONS'])
def criar_despesa():
    if request.method == 'OPTIONS':
        return jsonify({'success': True}), 200

    dados = strip_strings(request.get_json() or {})
    faltando = campos_presentes(dados, ['categoria', 'vencimento', 'valor', 'status', 'unidade'])
    if faltando:
        return jsonify({'error': f'Campos obrigatórios: {", ".join(faltando)}'}), 400

    # Converte DD/MM/YYYY → YYYY-MM-DD para o PostgreSQL (coluna DATE)
    vencimento_raw = dados['vencimento']
    if '/' in vencimento_raw:
        partes = vencimento_raw.split('/')
        if len(partes) == 3:
            vencimento_raw = f'{partes[2]}-{partes[1]}-{partes[0]}'

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO despesas (categoria, vencimento, valor, status, unidade) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (dados['categoria'], vencimento_raw, dados['valor'], dados['status'], dados['unidade'])
        )
        novo_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        return jsonify({'success': True, 'id': novo_id}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        put_conn(conn)

@app.route('/api/despesas/<int:despesa_id>', methods=['PUT'])
def atualizar_despesa_simples(despesa_id):
    dados = strip_strings(request.get_json() or {})
    campos_permitidos = {'categoria', 'vencimento', 'valor', 'status'}
    updates = {k: v for k, v in dados.items() if k in campos_permitidos}
    if not updates:
        return jsonify({'error': 'Nenhum campo válido para atualizar'}), 400

    # Converte DD/MM/YYYY → YYYY-MM-DD para o PostgreSQL (coluna DATE)
    if 'vencimento' in updates and '/' in str(updates['vencimento']):
        partes = updates['vencimento'].split('/')
        if len(partes) == 3:
            updates['vencimento'] = f'{partes[2]}-{partes[1]}-{partes[0]}'

    set_clause = ', '.join(f"{col} = %s" for col in updates)
    valores = list(updates.values()) + [despesa_id]

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE despesas SET {set_clause} WHERE id = %s", valores)
        if cursor.rowcount == 0:
            return jsonify({'error': 'Despesa não encontrada'}), 404
        conn.commit()
        cursor.close()
        return jsonify({'success': True}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        put_conn(conn)

@app.route('/api/despesas/<int:despesa_id>', methods=['DELETE'])
def excluir_despesa_simples(despesa_id):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM despesas WHERE id = %s", (despesa_id,))
        if cursor.rowcount == 0:
            return jsonify({'error': 'Despesa não encontrada'}), 404
        conn.commit()
        cursor.close()
        return jsonify({'success': True}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        put_conn(conn)

# ─────────────────────────────────────────────────────────────────────────────
# DESPESAS (protegido — só admin)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/financeiro/despesas', methods=['POST', 'OPTIONS'])
@requer_admin
def nova_despesa():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    dados = strip_strings(request.get_json() or {})
    faltando = campos_presentes(dados, ['categoria', 'vencimento', 'valor', 'status', 'unidade'])
    if faltando:
        return jsonify({'status': 'erro', 'mensagem': f'Campos obrigatórios: {", ".join(faltando)}'}), 400

    # Converte DD/MM/YYYY → YYYY-MM-DD para o PostgreSQL (coluna DATE)
    vencimento_raw = dados['vencimento']
    if '/' in vencimento_raw:
        partes = vencimento_raw.split('/')
        if len(partes) == 3:
            vencimento_raw = f'{partes[2]}-{partes[1]}-{partes[0]}'

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO despesas (categoria, vencimento, valor, status, unidade) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (dados['categoria'], vencimento_raw, dados['valor'], dados['status'], dados['unidade'])
        )
        novo_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        return jsonify({'status': 'sucesso', 'id': novo_id}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

@app.route('/api/financeiro/despesas/<int:despesa_id>', methods=['PUT'])
@requer_admin
def atualizar_despesa(despesa_id):
    dados = strip_strings(request.get_json() or {})
    if not dados:
        return jsonify({'status': 'erro', 'mensagem': 'Nenhum campo para atualizar'}), 400

    # Monta SET dinâmico apenas com os campos permitidos
    campos_permitidos = {'categoria', 'vencimento', 'valor', 'status'}
    updates = {k: v for k, v in dados.items() if k in campos_permitidos}
    if not updates:
        return jsonify({'status': 'erro', 'mensagem': 'Nenhum campo válido'}), 400

    # Converte DD/MM/YYYY → YYYY-MM-DD para o PostgreSQL (coluna DATE)
    if 'vencimento' in updates and '/' in str(updates['vencimento']):
        partes = updates['vencimento'].split('/')
        if len(partes) == 3:
            updates['vencimento'] = f'{partes[2]}-{partes[1]}-{partes[0]}'

    set_clause = ', '.join(f"{col} = %s" for col in updates)
    valores = list(updates.values()) + [despesa_id]

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE despesas SET {set_clause} WHERE id = %s", valores)
        if cursor.rowcount == 0:
            return jsonify({'status': 'erro', 'mensagem': 'Despesa não encontrada'}), 404
        conn.commit()
        cursor.close()
        return jsonify({'status': 'sucesso'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

@app.route('/api/financeiro/despesas/<int:despesa_id>', methods=['DELETE'])
@requer_admin
def excluir_despesa(despesa_id):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM despesas WHERE id = %s", (despesa_id,))
        if cursor.rowcount == 0:
            return jsonify({'status': 'erro', 'mensagem': 'Despesa não encontrada'}), 404
        conn.commit()
        cursor.close()
        return jsonify({'status': 'sucesso'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

# ─────────────────────────────────────────────────────────────────────────────
# DADOS FINANCEIROS (protegido — só admin)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/financeiro/dados', methods=['GET'])
@requer_admin
def dados_financeiros():
    unidade = (request.args.get('unidade') or '').strip()
    if not unidade:
        return jsonify({'status': 'erro', 'mensagem': 'Parâmetro unidade obrigatório'}), 400

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, categoria, TO_CHAR(vencimento, 'DD/MM/YYYY'), valor, status FROM despesas WHERE unidade = %s ORDER BY vencimento ASC;",
            (unidade,)
        )
        despesas_db = cursor.fetchall()
        lista_despesas = [
            {'id': d[0], 'categoria': d[1], 'vencimento': d[2], 'valor': float(d[3]), 'status': d[4]}
            for d in despesas_db
        ]
        total_despesas = sum(d['valor'] for d in lista_despesas if d['status'] != 'Cancelado')

        cursor.execute(
            "SELECT plano, COUNT(*) FROM alunos WHERE unidade = %s GROUP BY plano;",
            (unidade,)
        )
        planos_db = cursor.fetchall()
        cursor.close()

        contagem = {'anual': 0, 'semestral': 0, 'mensal': 0}
        for p in planos_db:
            nome = (p[0] or '').lower()
            if 'anual' in nome:
                contagem['anual'] += p[1]
            elif 'semestral' in nome:
                contagem['semestral'] += p[1]
            else:
                contagem['mensal'] += p[1]

        total_alunos = sum(contagem.values())
        distribuicao = {'anual': 0, 'semestral': 0, 'mensal': 0}
        if total_alunos > 0:
            distribuicao['anual']     = round((contagem['anual']     / total_alunos) * 100)
            distribuicao['semestral'] = round((contagem['semestral'] / total_alunos) * 100)
            distribuicao['mensal']    = 100 - (distribuicao['anual'] + distribuicao['semestral'])

        # Cálculo real — sem valor falso de fallback
        faturamento = (contagem['anual'] * 89.90) + (contagem['semestral'] * 99.90) + (contagem['mensal'] * 119.90)
        receita     = faturamento - total_despesas
        inadimplencia = faturamento * 0.034

        return jsonify({
            'despesas': lista_despesas,
            'planDistribution': distribuicao,
            'resumo': {'faturamento': faturamento, 'receita': receita, 'inadimplencia': inadimplencia}
        }), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

# ─────────────────────────────────────────────────────────────────────────────
# PROFISSIONAIS (protegido — só admin)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/profissionais', methods=['GET'])
@requer_admin
def listar_profissionais():
    unidade = (request.args.get('unidade') or '').strip()
    if not unidade:
        return jsonify({'status': 'erro', 'mensagem': 'Parâmetro unidade obrigatório'}), 400

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT nome, cargo, TO_CHAR(horario_inicio, 'HH24:MI'), TO_CHAR(horario_fim, 'HH24:MI'), dias_semana FROM profissionais WHERE unidade = %s",
            (unidade,)
        )
        equipe = cursor.fetchall()
        cursor.close()
        lista = [{'nome': e[0], 'cargo': e[1], 'inicio': e[2], 'fim': e[3], 'dias': e[4]} for e in equipe]
        return jsonify(lista), 200
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    finally:
        put_conn(conn)

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug)
