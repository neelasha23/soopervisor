import os
import tarfile
import subprocess
from pathlib import Path
from unittest.mock import Mock, call, ANY
from glob import glob
import sys

import yaml
import pytest
from click import ClickException
from ploomber.spec import DAGSpec
from ploomber.executors import Serial
from ploomber.io._commander import Commander, CommanderStop

from soopervisor.commons import source, conda, dependencies
from soopervisor import commons
from soopervisor.exceptions import MissingDockerfileError
from soopervisor.abc import AbstractDockerConfig

from conftest import CustomCommander


class ConcreteDockerConfig(AbstractDockerConfig):
    @classmethod
    def get_backend_value(self):
        return 'backend-value'


@pytest.fixture
def cmdr():
    with Commander() as cmdr:
        yield cmdr


def git_init():
    # to prevent overwriting the repo's settings
    if 'soopervisor' in str(Path('.').resolve()):
        raise ValueError('This doesnt look like a tmp directory. '
                         'Did you forget the tmp_empty fixture?')

    subprocess.check_call(['git', 'init'])
    subprocess.check_call(['git', 'config', 'user.email', 'ci@ploomberio'])
    subprocess.check_call(['git', 'config', 'user.name', 'Ploomber'])
    subprocess.check_call(['git', 'add', '--all'])
    subprocess.check_call(['git', 'commit', '-m', 'commit'])


def git_commit():
    subprocess.check_call(['git', 'add', '--all'])
    subprocess.check_call(['git', 'commit', '-m', 'commit'])


def test_glob_all_excludes_directories(tmp_empty):
    Path('dir').mkdir()
    Path('dir', 'a').touch()

    assert set(Path(p) for p in source.glob_all('.')) == {Path('dir', 'a')}


def test_global_all_excludes_from_arg(tmp_empty):
    Path('dir').mkdir()
    Path('dir', 'a').touch()
    Path('excluded').mkdir()
    Path('excluded', 'should-not-appear').touch()

    assert set(Path(p) for p in source.glob_all('.', exclude='excluded')) == {
        Path('dir', 'a')
    }


def test_copy(cmdr, tmp_empty):
    Path('file').touch()
    Path('dir').mkdir()
    Path('dir', 'another').touch()
    git_init()

    source.copy(cmdr, '.', 'dist')

    expected = set(Path(p) for p in (
        'dist/file',
        'dist/dir/another',
    ))

    assert set(Path(p) for p in source.glob_all('dist')) == expected


def test_copy_with_gitignore(cmdr, tmp_empty):
    Path('file').touch()
    Path('ignoreme').touch()

    Path('.gitignore').write_text('ignoreme')
    git_init()
    source.copy(cmdr, '.', 'dist')

    expected = set({Path('dist/file')})
    assert set(Path(p) for p in source.glob_all('dist')) == expected


def test_error_if_exclude_and_include_overlap(cmdr, tmp_empty):

    with pytest.raises(ClickException) as excinfo:
        source.copy(cmdr, '.', 'dist', exclude=['file'], include=['file'])

    expected = ("include and exclude must "
                "not have overlapping elements: {'file'}")
    assert expected == str(excinfo.value)


def test_override_git_with_exclude(cmdr, tmp_empty):
    Path('file').touch()
    Path('secrets.txt').touch()

    # let git track everything
    Path('.gitignore').touch()
    git_init()

    # exclude some file
    source.copy(cmdr, '.', 'dist', exclude=['file'])

    expected = set({Path('dist/secrets.txt')})
    assert set(Path(p) for p in source.glob_all('dist')) == expected


def test_copy_override_gitignore_with_include(cmdr, tmp_empty):
    Path('file').touch()
    Path('secrets.txt').touch()

    Path('.gitignore').write_text('secrets.txt')
    git_init()

    source.copy(cmdr, '.', 'dist', include=['secrets.txt'])

    expected = set(Path(p) for p in (
        'dist/file',
        'dist/secrets.txt',
    ))

    assert set(Path(p) for p in source.glob_all('dist')) == expected


def test_copy_override_gitignore_with_include_entire_folder(cmdr, tmp_empty):
    Path('file').touch()
    Path('dir').mkdir()
    Path('dir', 'secrets.txt').touch()
    Path('dir', 'more-secrets.txt').touch()

    Path('.gitignore').write_text('dir')
    git_init()

    source.copy(cmdr, '.', 'dist', include=['dir'])

    expected = set(
        Path(p) for p in (
            'dist/file',
            'dist/dir/secrets.txt',
            'dist/dir/more-secrets.txt',
        ))

    assert set(Path(p) for p in source.glob_all('dist')) == expected


def test_no_git_but_exclude(cmdr, tmp_empty):
    Path('file').touch()
    Path('secrets.txt').touch()

    source.copy(cmdr, '.', 'dist', exclude=['secrets.txt'])

    expected = set(Path(p) for p in ('dist/file', ))

    assert set(Path(p) for p in source.glob_all('dist')) == expected


def test_no_git_but_exclude_entire_folder(cmdr, tmp_empty):
    Path('file').touch()
    Path('dir').mkdir()
    Path('dir', 'secrets.txt').touch()
    Path('dir', 'more-secrets.txt').touch()

    source.copy(cmdr, '.', 'dist', exclude=['dir'])

    expected = set(Path(p) for p in ('dist/file', ))
    assert set(Path(p) for p in source.glob_all('dist')) == expected


def test_ignores_pycache(cmdr, tmp_empty):
    Path('file').touch()
    dir_ = Path('__pycache__')
    dir_.mkdir()
    (dir_ / 'file').touch()
    (dir_ / 'another').touch()
    dir_another = Path('subdir', '__pycache__')
    dir_another.mkdir(parents=True)
    (dir_another / 'file').touch()
    (dir_another / 'another').touch()

    source.copy(cmdr, '.', 'dist')

    expected = set(Path(p) for p in ('dist/file', ))
    assert set(Path(p) for p in source.glob_all('dist')) == expected


def test_git_tracked_files(tmp_empty):
    Path('file').touch()
    Path('dir').mkdir()
    Path('dir', 'another').touch()
    git_init()

    assert {'dir/another', 'file'} == set(source.git_tracked_files()[0])


def test_warns_if_fails_to_get_git_tracked_files(tmp_empty, capsys):
    Path('file').touch()
    Path('secrets.txt').touch()

    with Commander() as cmdr:
        source.copy(cmdr, '.', 'dist')

    captured = capsys.readouterr()

    assert 'Unable to get git tracked files' in captured.out


def test_warns_on_dirty_git(tmp_empty, capsys):
    Path('file').touch()
    Path('secrets.txt').touch()

    Path('.gitignore').write_text('secrets.txt')
    git_init()

    Path('new-file').touch()

    with Commander() as cmdr:
        source.copy(cmdr, '.', 'dist')

    captured = capsys.readouterr()

    assert 'Your git repository contains uncommitted' in captured.out


def test_errors_if_no_tracked_files(tmp_empty):

    Path('file').touch()
    git_init()

    dir_ = Path('dir')
    dir_.mkdir()
    os.chdir(dir_)

    Path('another').touch()

    with pytest.raises(ClickException) as excinfo:
        with Commander() as cmdr:
            source.copy(cmdr, '.', 'dist')

    expected = ('Running inside a git repository, but no files in '
                'the current working directory are tracked by git. Commit the '
                'files to include them in the Docker image or pass the '
                '--ignore-git flag to soopervisor export')
    assert str(excinfo.value) == expected


def test_copy_ignore_git(tmp_empty):
    Path('file').touch()
    git_init()

    dir_ = Path('dir')
    dir_.mkdir()
    os.chdir(dir_)

    Path('another').touch()

    with Commander() as cmdr:
        source.copy(cmdr, '.', 'dist', ignore_git=True)

    assert Path('dist', 'another').is_file()


def test_compress_dir(tmp_empty):
    dir = Path('dist', 'project-name')
    dir.mkdir(parents=True)
    (dir / 'file').touch()

    with Commander() as cmdr:
        source.compress_dir(cmdr, 'dist/project-name',
                            'dist/project-name.tar.gz')

    with tarfile.open('dist/project-name.tar.gz', 'r:gz') as tar:
        tar.extractall('.')

    expected = {Path('project-name/file')}
    assert set(Path(p) for p in source.glob_all('project-name')) == expected


def test_compress_warns_if_output_too_big(tmp_empty, monkeypatch, capsys):
    # mock a file of 6MB
    monkeypatch.setattr(source.os.path, 'getsize',
                        Mock(return_value=1024 * 1024 * 6))

    dir = Path('dist', 'project-name')
    dir.mkdir(parents=True)
    (dir / 'file').touch()

    with Commander() as cmdr:
        source.compress_dir(cmdr, 'dist/project-name',
                            'dist/project-name.tar.gz')

    captured = capsys.readouterr()
    expected = ("The project's source code 'dist/project-name.tar.gz' "
                "is larger than 5MB")
    assert expected in captured.out


@pytest.mark.parametrize('env_yaml, expected', [
    [{
        'dependencies': ['a', 'b', {
            'pip': ['c', 'd']
        }]
    }, ['c', 'd']],
    [{
        'dependencies': [{
            'pip': ['y', 'z']
        }, 'a', 'b']
    }, ['y', 'z']],
])
def test_extract_pip_from_env_yaml(tmp_empty, env_yaml, expected):
    Path('environment.yml').write_text(yaml.safe_dump(env_yaml))
    assert conda.extract_pip_from_env_yaml('environment.yml') == expected


def test_error_extract_pip_missing_dependencies_section():
    Path('environment.yml').write_text(yaml.safe_dump({}))

    with pytest.raises(ClickException) as excinfo:
        conda.extract_pip_from_env_yaml('environment.yml')

    msg = ('Cannot extract pip dependencies from environment.lock.yml: '
           'missing dependencies section')
    assert msg == str(excinfo.value)


def test_error_extract_pip_missing_pip_dict():
    Path('environment.yml').write_text(
        yaml.safe_dump({'dependencies': ['a', 'b']}))

    with pytest.raises(ClickException) as excinfo:
        conda.extract_pip_from_env_yaml('environment.yml')

    msg = ('Cannot extract pip dependencies from environment.lock.yml: '
           'missing dependencies.pip section')
    assert msg == str(excinfo.value)


def test_error_extract_pip_unexpected_pip_list():
    Path('environment.yml').write_text(
        yaml.safe_dump({'dependencies': ['a', 'b', {
            'pip': 1
        }]}))

    with pytest.raises(ClickException) as excinfo:
        conda.extract_pip_from_env_yaml('environment.yml')

    msg = ('Cannot extract pip dependencies from environment.lock.yml: '
           'unexpected dependencies.pip value. Expected a list of '
           'dependencies, got: 1')
    assert msg == str(excinfo.value)


@pytest.fixture
def dag_build():
    dag = DAGSpec.find().to_dag()
    dag.executor = Serial(build_in_subprocess=False)
    dag.render().build()


@pytest.mark.parametrize('mode, tasks_expected, args_expected', [
    ['incremental', {}, ['--entry-point', 'pipeline.yaml']],
    [
        'regular', {
            'root': [],
            'another': ['root']
        }, ['--entry-point', 'pipeline.yaml']
    ],
    [
        'force', {
            'root': [],
            'another': ['root']
        }, ['--entry-point', 'pipeline.yaml', '--force']
    ],
])
def test_load_tasks(cmdr, tmp_fast_pipeline, add_current_to_sys_path,
                    dag_build, mode, tasks_expected, args_expected):
    tasks, args = commons.load_tasks(cmdr=cmdr, mode=mode)
    assert tasks == tasks_expected
    assert args == args_expected


@pytest.mark.parametrize('mode, tasks_expected, args_expected', [
    ['incremental', {
        'another': []
    }, ['--entry-point', 'pipeline.yaml']],
    [
        'regular', {
            'root': [],
            'another': ['root']
        }, ['--entry-point', 'pipeline.yaml']
    ],
    [
        'force', {
            'root': [],
            'another': ['root']
        }, ['--entry-point', 'pipeline.yaml', '--force']
    ],
])
def test_load_tasks_missing_remote_metadata(cmdr, tmp_fast_pipeline,
                                            add_current_to_sys_path, dag_build,
                                            mode, tasks_expected,
                                            args_expected):
    Path('remote', 'out', 'another').unlink()
    tasks, args = commons.load_tasks(cmdr=cmdr, mode=mode)
    assert tasks == tasks_expected
    assert args == args_expected


def test_invalid_mode(cmdr, tmp_fast_pipeline):
    with pytest.raises(ValueError) as excinfo:
        commons.load_tasks(cmdr=cmdr, mode='unknown')

    assert 'mode must be one of' in str(excinfo.value)


def test_loads_pipeline_with_name(cmdr, tmp_fast_pipeline):
    os.rename('pipeline.yaml', 'pipeline.train.yaml')

    # we need this to set our project root
    Path('pipeline.yaml').touch()

    _, args = commons.load_tasks(cmdr, name='train')
    assert args == ['--entry-point', 'pipeline.train.yaml']


def test_loads_pipeline_in_package_with_name(cmdr, backup_packaged_project):
    os.rename(Path('src', 'my_project', 'pipeline.yaml'),
              Path('src', 'my_project', 'pipeline.train.yaml'))
    _, args = commons.load_tasks(cmdr, name='train')

    assert args == [
        '--entry-point',
        str(Path('src/my_project/pipeline.train.yaml'))
    ]


def test_check_lock_files_exist(tmp_empty):

    with pytest.raises(ClickException) as excinfo:
        dependencies.check_lock_files_exist()

    expected = ('Expected requirements.lock.txt or environment.lock.yml at '
                'the root directory')
    assert expected in str(excinfo.value)


def test_error_if_missing_dockerfile(tmp_empty):
    with pytest.raises(MissingDockerfileError) as excinfo:
        commons.docker.build(e=Mock(),
                             cfg=Mock(),
                             env_name='some_name',
                             until=Mock(),
                             entry_point=Mock())

    assert excinfo.value.env_name == 'some_name'


def test_cp_ploomber_home(tmp_empty, monkeypatch):
    monkeypatch.setattr(commons.docker.telemetry, 'get_home_dir',
                        lambda: 'home-dir')

    home = Path('home-dir')
    home.mkdir()
    (home / 'some-file').touch()
    (home / 'dir').mkdir()
    (home / 'dir' / 'another-file').touch()

    commons.docker.cp_ploomber_home('some-env')
    files = glob('some-env/dist/ploomber/**', recursive=True)

    assert files == [
        'some-env/dist/ploomber/',
        'some-env/dist/ploomber/dir',
        'some-env/dist/ploomber/dir/another-file',
        'some-env/dist/ploomber/some-file',
    ]


def test_cp_ploomber_home_creates_empty_folder_if_home_does_not_exist(
        tmp_empty, monkeypatch):
    monkeypatch.setattr(commons.docker.telemetry, 'get_home_dir',
                        lambda: 'some-missing-directory')

    commons.docker.cp_ploomber_home('some-env')

    assert Path('some-env', 'dist', 'ploomber').is_dir()


def test_docker_build_copies_ploomber_home(tmp_sample_project, monkeypatch):
    monkeypatch.setattr(commons.docker.telemetry, 'get_home_dir',
                        lambda: 'home-dir')

    home = Path('home-dir')
    home.mkdir()
    (home / 'some-file').touch()
    (home / 'dir').mkdir()
    (home / 'dir' / 'another-file').touch()

    Path('some-env').mkdir()
    Path('some-env', 'Dockerfile').touch()

    with CustomCommander(workspace='some-env') as cmdr:
        commons.docker.build(cmdr,
                             ConcreteDockerConfig(),
                             'some-env',
                             until=None,
                             entry_point='pipeline.yaml')

        files = glob('dist/ploomber/**', recursive=True)

    assert files == [
        'dist/ploomber/',
        'dist/ploomber/dir',
        'dist/ploomber/dir/another-file',
        'dist/ploomber/some-file',
    ]


_TEST_DAG = ('from ploomber.spec import DAGSpec; '
             'print("File" in DAGSpec("pipeline.yaml").to_dag().clients)')

_DOCKER_BUILD_CALL = call('docker',
                          'build',
                          '.',
                          '--tag',
                          'sample_project:latest',
                          description='Building image')

_DOCKER_TEST_STATUS = call(
    'docker',
    'run',
    'sample_project:latest',
    'ploomber',
    'status',
    '--entry-point',
    'pipeline.yaml',
    description='Testing image',
    error_message='Error while testing your docker image with',
    hint=ANY)

_DOCKER_TEST_CLIENT = call('docker',
                           'run',
                           'sample_project:latest',
                           'python',
                           '-c',
                           _TEST_DAG,
                           description='Testing File client',
                           error_message='Missing File client',
                           hint=ANY,
                           capture_output=True,
                           expected_output='True\n',
                           show_cmd=False)


@pytest.mark.parametrize('until, skip_tests, cfg, expected', [
    [
        None, False, {},
        [_DOCKER_BUILD_CALL, _DOCKER_TEST_STATUS, _DOCKER_TEST_CLIENT]
    ],
    [None, True, {}, [_DOCKER_BUILD_CALL]],
    [
        None, False,
        dict(repository='repo.domain.com/project'),
        [
            _DOCKER_BUILD_CALL, _DOCKER_TEST_STATUS, _DOCKER_TEST_CLIENT,
            call('docker',
                 'tag',
                 'sample_project:latest',
                 'repo.domain.com/project:latest',
                 description='Tagging'),
            call('docker',
                 'push',
                 'repo.domain.com/project:latest',
                 description='Pushing image')
        ]
    ],
    [
        None, False,
        dict(repository='repo.domain.com/project:1.2'),
        [
            _DOCKER_BUILD_CALL, _DOCKER_TEST_STATUS, _DOCKER_TEST_CLIENT,
            call('docker',
                 'tag',
                 'sample_project:latest',
                 'repo.domain.com/project:1.2',
                 description='Tagging'),
            call('docker',
                 'push',
                 'repo.domain.com/project:1.2',
                 description='Pushing image')
        ]
    ],
],
                         ids=[
                             'no-repo',
                             'skip-tests',
                             'with-repo',
                             'with-repo-and-tag',
                         ])
def test_docker_commands(tmp_sample_project, until, skip_tests, cfg, expected):
    Path('some-env').mkdir()
    Path('some-env', 'Dockerfile').touch()

    cmdr = Mock()

    commons.docker.build(cmdr,
                         ConcreteDockerConfig(**cfg),
                         'some-env',
                         until=until,
                         entry_point='pipeline.yaml',
                         skip_tests=skip_tests)

    assert cmdr.run.call_args_list == expected


@pytest.mark.parametrize('until, skip_tests, cfg, expected', [
    [
        'build', False,
        dict(repository='repo.domain.com/project'),
        [
            _DOCKER_BUILD_CALL,
            _DOCKER_TEST_STATUS,
            _DOCKER_TEST_CLIENT,
        ]
    ],
    [
        'push', False,
        dict(repository='repo.domain.com/project:1.2'),
        [
            _DOCKER_BUILD_CALL, _DOCKER_TEST_STATUS, _DOCKER_TEST_CLIENT,
            call('docker',
                 'tag',
                 'sample_project:latest',
                 'repo.domain.com/project:1.2',
                 description='Tagging'),
            call('docker',
                 'push',
                 'repo.domain.com/project:1.2',
                 description='Pushing image')
        ]
    ],
],
                         ids=[
                             'with-repo-until-build',
                             'with-repo-until-push',
                         ])
def test_docker_commands_until(tmp_sample_project, until, skip_tests, cfg,
                               expected):
    Path('some-env').mkdir()
    Path('some-env', 'Dockerfile').touch()

    cmdr = Mock()

    with pytest.raises(CommanderStop):
        commons.docker.build(cmdr,
                             ConcreteDockerConfig(**cfg),
                             'some-env',
                             until=until,
                             entry_point='pipeline.yaml',
                             skip_tests=skip_tests)

    assert cmdr.run.call_args_list == expected


def test_dockerfile(tmp_empty):
    with Commander(workspace='some-env',
                   templates_path=('soopervisor', 'assets')) as e:
        e.copy_template('docker/Dockerfile', conda=False, setup_py=False)

    os.chdir('some-env')

    Path('dist').mkdir()
    Path('file').touch()

    with tarfile.open(Path('dist', 'some-file.tar.gz'), 'w:gz') as tar:
        tar.add(os.path.basename('file'))

    Path('requirements.lock.txt').write_text('pkgmt')

    Path('dist', 'ploomber').mkdir(parents=True)
    Path('dist', 'ploomber', 'file').touch()

    subprocess.check_call(['docker', 'build', '.', '--tag', 'testing'])

    

    # check uncompressed tarball
    result = subprocess.check_output(
        ['docker', 'run', '-i', '-t', 'testing', 'ls', '/project/file'])
    result = result.decode(sys.stdout.encoding)

    # check ploomber home
    result = subprocess.check_output(
        ['docker', 'run', '-i', '-t', 'testing', 'ls', '/root/.ploomber'])
    result = result.decode(sys.stdout.encoding)

    # check ploomber home
    result = subprocess.check_output(
        ['docker', 'run', '-i', '-t', 'testing', 'pip', 'freeze'])
    result = result.decode(sys.stdout.encoding)
