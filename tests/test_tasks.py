from datetime import date
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'

def test_create_list_update_delete_task():
    created = client.post('/api/tasks', json={
        'title': '  Test task   with spaces ', 'description': ' example ', 'priority': 'high',
        'status': 'todo', 'due_date': date.today().isoformat()
    })
    assert created.status_code == 201, created.text
    task = created.json()
    assert task['title'] == 'Test task with spaces'
    assert task['description'] == 'example'
    task_id = task['id']

    listing = client.get('/api/tasks')
    assert listing.status_code == 200
    assert any(item['id'] == task_id for item in listing.json())

    updated = client.patch(f'/api/tasks/{task_id}', json={'status': 'done', 'priority': 'low'})
    assert updated.status_code == 200
    assert updated.json()['status'] == 'done'
    assert updated.json()['priority'] == 'low'

    deleted = client.delete(f'/api/tasks/{task_id}')
    assert deleted.status_code == 204
    missing = client.patch(f'/api/tasks/{task_id}', json={'status': 'todo'})
    assert missing.status_code == 404

def test_rejects_blank_title():
    response = client.post('/api/tasks', json={'title': '    '})
    assert response.status_code == 422

def test_rejects_invalid_status():
    response = client.post('/api/tasks', json={'title': 'Bad status', 'status': 'waiting'})
    assert response.status_code == 422

def test_chat_handles_unknown_request():
    response = client.post('/api/chat', json={'message': 'How is the weather on Mars?'})
    assert response.status_code == 200
    assert 'create' in response.json()['reply'].lower() or 'task' in response.json()['reply'].lower()
