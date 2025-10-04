from __future__ import annotations

from pathlib import Path
import typer

from .render import render_client_quote


app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def price(
    project_dir: str = typer.Argument(..., help="Path to project directory"),
    out: str | None = typer.Option(None, help="Output HTML path"),
    configs: str = typer.Option("configs", help="Configs directory (policy, rates)"),
):
    """Render a client-facing HTML quote from project overrides and configs."""
    project_path = Path(project_dir)
    out_path = Path(out) if out else None
    configs_dir = Path(configs)

    out_file = render_client_quote(project_path, out_path=out_path, configs_dir=configs_dir)
    typer.echo(f"Wrote {out_file}")


if __name__ == "__main__":
    app()

