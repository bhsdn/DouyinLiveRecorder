# -*- coding: utf-8 -*-
from recorder_core.settings import BackupManager, ConfigStore, IniDocument, parse_bool


def test_load_creates_config_from_example_and_parses_types(home):
    store = ConfigStore(home / 'config' / 'config.ini', home / 'config' / 'config.ini.example')
    settings = store.load()
    assert settings.quality == '原画'
    assert settings.save_format_code == 'TS'
    assert settings.loop_interval == 300
    assert settings.disk_space_limit == 1.0
    assert settings.split_enabled is True
    assert settings.cookie('douyin') == ''
    assert len(settings.cookies) == 50
    assert 'tiktok' in settings.proxy_platform_list


def test_save_keeps_comments_and_unrelated_lines(home):
    store = ConfigStore(home / 'config' / 'config.ini', home / 'config' / 'config.ini.example')
    store.load()
    settings = store.save({'loop_interval': 60, 'split_enabled': False, 'push_channels': ['微信', 'TG']},
                          {'douyin': 'ttwid=abc%7C123'})
    text = (home / 'config' / 'config.ini').read_text(encoding='utf-8-sig')
    assert '# 可选微信|钉钉|tg|邮箱|bark|ntfy|pushplus 可填多个' in text
    assert '循环时间(秒) = 60' in text
    assert '分段录制是否开启 = 否' in text
    assert '抖音cookie = ttwid=abc%7C123' in text
    assert settings.loop_interval == 60
    assert settings.push_channel_list == ['微信', 'TG']
    assert settings.cookie('douyin') == 'ttwid=abc%7C123'


def test_missing_keys_and_sections_are_added_with_defaults(tmp_path):
    path = tmp_path / 'config.ini'
    path.write_text('[录制设置]\n循环时间(秒) = 10\n', encoding='utf-8-sig')
    settings = ConfigStore(path).load()
    assert settings.loop_interval == 10
    assert settings.max_request == 3
    doc = IniDocument.load(path)
    assert doc.get('推送配置', '开播推送开启(是/否)') == '是'
    assert doc.get('Web', '端口') == '8686'


def test_keys_match_case_insensitively_like_configparser(tmp_path):
    path = tmp_path / 'config.ini'
    path.write_text('[推送配置]\nsmtp邮件服务器 = smtp.qq.com\n', encoding='utf-8-sig')
    assert ConfigStore(path).load().email_host == 'smtp.qq.com'


def test_parse_bool():
    assert parse_bool('是', False) is True
    assert parse_bool('否', True) is False
    assert parse_bool('', True) is True
    assert parse_bool('乱填', False) is False


def test_backup_manager_keeps_latest_copies(tmp_path):
    source = tmp_path / 'config.ini'
    backup_dir = tmp_path / 'backup'
    manager = BackupManager([source], backup_dir, limit=2)
    for i in range(4):
        source.write_text(f'v{i}', encoding='utf-8')
        created = manager.run_once()
        assert len(created) == 1
        created[0].rename(backup_dir / f'config.ini_{i}')
    assert manager.run_once() == []
    assert len(list(backup_dir.iterdir())) == 2
