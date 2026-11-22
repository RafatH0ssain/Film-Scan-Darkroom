from fsd.paths import APP_DIR_NAME, REPO_ROOT, user_data_dir


def test_user_data_dir_from_source_is_repo_root():
    assert user_data_dir() == REPO_ROOT


def test_app_dir_name():
    assert APP_DIR_NAME == "Film Scan Darkroom"
