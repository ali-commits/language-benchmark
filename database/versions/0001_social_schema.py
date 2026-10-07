from pathlib import Path
from alembic import op
revision='0001'
down_revision=None
branch_labels=None
depends_on=None

def upgrade() -> None:
    for statement in Path(__file__).parents[1].joinpath('schema.sql').read_text().split(';'):
        if statement.strip(): op.execute(statement)

def downgrade() -> None:
    for name in ('benchmark_identity','comments','likes','follows','posts','users'):
        op.execute(f'DROP TABLE {name}')
