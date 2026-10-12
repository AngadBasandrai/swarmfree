"""Owned, deterministic provider fixtures for tests; never imported by the app."""
import asyncio
import re
from swarm.providers import Providers
from swarm.schema import ProfileInput


def install_model_fixture(store, vault, patch=None, response=None):
    original_stream = Providers.compatible
    profile = Providers(store, vault, 'http://127.0.0.1:1').add(ProfileInput(
        provider='google', label='Test provider fixture', api_key='owned-test-placeholder',
        model='fixture-model', quota_scope='owned-test-fixture', free_eligible=True,
        ephemeral=True, requests_per_minute=600, input_usd_per_million=0, output_usd_per_million=0))

    async def stream(self, profile, messages):
        if profile['label'] != 'Test provider fixture':
            async for chunk in original_stream(self, profile, messages):
                yield chunk
            return
        system = next(m['content'] for m in messages if m['role'] == 'system')
        identity = re.search(r'^You are (.*?)\. Role: (.*?)\.\n', system)
        name, role = identity.groups() if identity else ('Agent', 'Help')
        users = [m['content'] for m in messages if m['role'] == 'user']
        task_message = next((m for m in users if m.startswith('Task:\n')), users[0] if users else '')
        task = task_message.removeprefix('Task:\n').split('\n\nCompleted upstream work:')[0]
        upstream = task_message.partition('Completed upstream work:\n')[2]
        prior = [dict(name=match.group(1), content='') for match in re.finditer(r'(?:^|\n\n)([^\n:]+):\n', upstream)]
        steering = users[-1] if users and users[-1] != task_message else ''
        if response:
            async for chunk in response(name, role, task, prior, steering):
                yield chunk
            return
        text = f'Test provider fixture · {name}\n\nTask: {task}\n\n'
        if prior: text += 'Received work from: ' + ', '.join(p['name'] for p in prior) + '.\n\n'
        if steering: text += f'Your direction: {steering}\n\n'
        text += 'Deterministic test response; not live model output.'
        for offset in range(0, len(text), 32):
            await asyncio.sleep(.003)
            yield text[offset:offset + 32]

    async def no_models(url):
        return dict(connected=False, models=[], loaded=[])

    if patch:
        patch.setattr(Providers, 'compatible', stream)
        patch.setattr('swarm.engine.inventory', no_models)
    else:
        import swarm.engine
        Providers.compatible = stream
        swarm.engine.inventory = no_models
    return profile
